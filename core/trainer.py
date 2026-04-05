"""
core/trainer.py — Local GPT Fine-Tuning for amber-hunter v1.2.34

用 auto-research 优化的超参（N_HEAD=1, BLOCK_SIZE=96, N_EMBED=256）
在用户记忆数据上微调，为 recall 重排 / 自动标签 / 记忆抽取提供本地推理。

训练数据：901 个胶囊 + WAL sessions
模型输出：~/.amber-hunter/models/amber-gpt.pt
"""
from __future__ import annotations

import json, math, os, time, secrets, sys
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset

# ── 路径 ─────────────────────────────────────────────────────
HOME = Path.home()
MODEL_DIR = HOME / ".amber-hunter" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = MODEL_DIR / "amber-gpt.pt"
TOKENIZER_PATH = MODEL_DIR / "tokenizer.json"

# ── 优化超参（来自 auto-research exp_079）───────────────────
BLOCK_SIZE = 96
N_EMBED = 256
N_HEAD = 1
N_LAYER = 6
DROPOUT = 0.05
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 0.0
MAX_ITERS = 300  # 每轮 fine-tune 最大迭代（5分钟 wall clock）


# ── 简单 BPE Tokenizer ───────────────────────────────────────

class SimpleTokenizer:
    """
    基于频率的 word-piece tokenizer。
    从胶囊文本自动构建 ~3000 token 的词典。
    """
    def __init__(self):
        self.vocab: list[str] = []
        self.stoi: dict[str, int] = {}
        self.itos: dict[int, str] = {}

    @classmethod
    def from_texts(cls, texts: list[str], vocab_size: int = 3000) -> "SimpleTokenizer":
        """从文本语料库构建 tokenizer（支持中英文混合）"""
        from collections import Counter
        import re
        word_freq: Counter[str] = Counter()

        for text in texts:
            # 英文词（word boundary）
            english_words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text)
            word_freq.update(w.lower() for w in english_words)
            # 中文：按字符分词
            chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
            word_freq.update(chinese_chars)
            # 数字和符号（小词片）
            symbols = re.findall(r'[0-9]+|[^\sa-zA-Z0-9\u4e00-\u9fff]{1,3}', text)
            word_freq.update(s.lower() for s in symbols if len(s) > 0)
            # 2-gram 字符（捕获常用词组）
            for n in (2, 3):
                for i in range(len(text) - n + 1):
                    chunk = text[i:i+n]
                    if not chunk.strip():
                        continue
                    word_freq[chunk] += 1

        # 取最高频的词片
        sorted_words = sorted(word_freq.items(), key=lambda x: -x[1])
        vocab = ["<PAD>", "<UNK>", "<EOS>"] + [w for w, _ in sorted_words[:vocab_size - 3] if len(w) > 0]
        tok = cls()
        tok.vocab = vocab[:vocab_size]
        tok.stoi = {w: i for i, w in enumerate(tok.vocab)}
        tok.itos = {i: w for w, i in tok.stoi.items()}
        return tok

    def encode(self, text: str) -> list[int]:
        """将文本编码为 token id 序列（支持中英文混合）"""
        import re
        ids = []
        # 英文词
        english_words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text)
        for w in english_words:
            ids.append(self.stoi.get(w.lower(), self.stoi["<UNK>"]))
        # 中文：按字符
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        for c in chinese_chars:
            ids.append(self.stoi.get(c, self.stoi["<UNK>"]))
        ids.append(self.stoi["<EOS>"])
        return ids

    def decode(self, ids: list[int]) -> str:
        """将 token id 序列解码为文本"""
        words = [self.itos.get(i, "<UNK>") for i in ids if i != self.stoi.get("<PAD>", 0)]
        return " ".join(words)

    def save(self, path: Path):
        with open(path, "w") as f:
            json.dump({"vocab": self.vocab, "stoi": self.stoi}, f)

    @classmethod
    def load(cls, path: Path) -> "SimpleTokenizer":
        with open(path) as f:
            data = json.load(f)
        tok = cls()
        tok.vocab = data["vocab"]
        tok.stoi = data["stoi"]
        tok.itos = {int(k): v for k, v in data.get("itos", {}).items()}
        return tok


# ── Dataset ──────────────────────────────────────────────────

class CapsuleDataset(Dataset):
    """将胶囊列表转换为 (context, next_token) 训练对"""
    def __init__(self, token_ids: list[int], block_size: int):
        self.token_ids = token_ids
        self.block_size = block_size

    def __len__(self):
        return max(0, len(self.token_ids) - self.block_size)

    def __getitem__(self, idx):
        x = self.token_ids[idx:idx + self.block_size]
        y = self.token_ids[idx + 1:idx + self.block_size + 1]
        return torch.tensor(x, dtype=torch.long), torch.tensor(y, dtype=torch.long)


# ── GPT 模型 ────────────────────────────────────────────────

class Head(nn.Module):
    def __init__(self, head_size: int):
        super().__init__()
        self.key = nn.Linear(N_EMBED, head_size, bias=False)
        self.query = nn.Linear(N_EMBED, head_size, bias=False)
        self.value = nn.Linear(N_EMBED, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(BLOCK_SIZE, BLOCK_SIZE)))
        self.dropout = nn.Dropout(DROPOUT)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, T, C = x.shape
        k = self.key(x)
        q = self.query(x)
        wei = q @ k.transpose(-2, -1) * (C ** -0.5)
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float("-inf"))
        wei = torch.softmax(wei, dim=-1)
        wei = self.dropout(wei)
        v = self.value(x)
        return wei @ v


class MultiHeadAttention(nn.Module):
    def __init__(self, num_heads: int, head_size: int):
        super().__init__()
        self.heads = nn.ModuleList([Head(head_size) for _ in range(num_heads)])
        self.proj = nn.Linear(N_EMBED, N_EMBED, bias=False)
        self.dropout = nn.Dropout(DROPOUT)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = torch.cat([h(x) for h in self.heads], dim=-1)
        return self.dropout(self.proj(out))


class FeedFwd(nn.Module):
    def __init__(self, n_embed: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embed, 4 * n_embed),
            nn.GELU(),
            nn.Linear(4 * n_embed, n_embed),
            nn.Dropout(DROPOUT),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class Block(nn.Module):
    def __init__(self, n_embed: int, n_head: int):
        super().__init__()
        head_size = n_embed // n_head
        self.attn = MultiHeadAttention(n_head, head_size)
        self.ffwd = FeedFwd(n_embed)
        self.ln1 = nn.LayerNorm(n_embed)
        self.ln2 = nn.LayerNorm(n_embed)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln1(x))
        x = x + self.ffwd(self.ln2(x))
        return x


class AmberGPT(nn.Module):
    """
    GPT 模型，架构同 auto-research train.py。
    vocab_size 在实例化时指定。
    """
    def __init__(self, vocab_size: int):
        super().__init__()
        self.vocab_size = vocab_size
        self.token_embedding = nn.Embedding(vocab_size, N_EMBED)
        self.position_embedding = nn.Embedding(BLOCK_SIZE, N_EMBED)
        self.blocks = nn.Sequential(*[Block(N_EMBED, N_HEAD) for _ in range(N_LAYER)])
        self.ln = nn.LayerNorm(N_EMBED)
        self.lm_head = nn.Linear(N_EMBED, vocab_size, bias=False)
        # 权重绑定（共享 token embedding 和 lm_head）
        self.lm_head.weight = self.token_embedding.weight

    def forward(self, idx: torch.Tensor, targets: torch.Tensor | None = None):
        B, T = idx.shape
        tok_emb = self.token_embedding(idx)
        pos_emb = self.position_embedding(torch.arange(T, device=idx.device))
        x = tok_emb + pos_emb
        x = self.blocks(x)
        x = self.ln(x)
        logits = self.lm_head(x)
        loss = None
        if targets is not None:
            loss = torch.nn.functional.cross_entropy(
                logits.view(-1, logits.size(-1)), targets.view(-1)
            )
        return logits, loss

    @torch.no_grad()
    def encode(self, token_ids: list[int]) -> torch.Tensor:
        """返回 last token 的隐藏状态（用于特征提取）"""
        self.eval()
        idx = torch.tensor([token_ids[-BLOCK_SIZE:]], dtype=torch.long)
        if idx.device != self.lm_head.weight.device:
            idx = idx.to(self.lm_head.weight.device)
        tok_emb = self.token_embedding(idx)
        pos_emb = self.position_embedding(torch.arange(idx.shape[1], device=idx.device))
        x = tok_emb + pos_emb
        x = self.blocks(x)
        x = self.ln(x)
        # 返回 last token 的表示
        return x[0, -1, :]

    @torch.no_grad()
    def generate(self, primer: list[int], max_new: int = 20) -> list[int]:
        """自回归生成（用于测试）"""
        self.eval()
        idx = torch.tensor([primer[-BLOCK_SIZE:]], dtype=torch.long)
        for _ in range(max_new):
            logits, _ = self.forward(idx)
            logits = logits[0, -1, :]
            probs = torch.softmax(logits, dim=-1)
            next_tok = torch.multinomial(probs, 1).item()
            if next_tok == self.token_embedding.weight.shape[0] - 1:  # <EOS>
                break
            idx = torch.cat([idx, torch.tensor([[next_tok]])], dim=1)
        return idx[0].tolist()


# ── 训练 ─────────────────────────────────────────────────────

def build_training_tokens(tokenizer: SimpleTokenizer) -> list[int]:
    """从所有胶囊构建训练 token 序列"""
    all_tokens: list[int] = []
    texts = _load_all_capsule_texts()
    for text in texts:
        tokens = tokenizer.encode(text)
        all_tokens.extend(tokens)

    if not all_tokens:
        # WAL 回退
        from core.wal import read_wal_entries, WAL_FILE
        for line in open(WAL_FILE).readlines()[:1000]:
            try:
                entry = json.loads(line)
                text = entry.get("data", {}).get("text", "")[:200]
                all_tokens.extend(tokenizer.encode(text))
            except Exception:
                pass

    return all_tokens


def fine_tune(
    vocab_size: int = 3000,
    iterations: int = MAX_ITERS,
    lr: float = LEARNING_RATE,
    batch_size: int = 32,
    device: str | None = None,
    progress_callback=None,
) -> dict:
    """
    在用户胶囊数据上 fine-tune AmberGPT。
    返回 {"status": "ok", "iterations": int, "final_loss": float, "model_path": str}
    """
    torch.manual_seed(42)
    np.random.seed(42)

    # 设备选择
    if device is None:
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
    print(f"[trainer] Using device: {device}")

    # 1. 构建 tokenizer
    print("[trainer] Building tokenizer from capsules...")
    texts = _load_all_capsule_texts()
    tokenizer = SimpleTokenizer.from_texts(texts, vocab_size=vocab_size)
    tokenizer.save(TOKENIZER_PATH)
    print(f"[trainer] Vocab size: {len(tokenizer.vocab)}")

    # 2. 构建训练数据
    print("[trainer] Building training tokens...")
    all_tokens = build_training_tokens(tokenizer)
    if len(all_tokens) < BLOCK_SIZE * 10:
        return {"status": "error", "error": f"Only {len(all_tokens)} tokens, need more data"}
    print(f"[trainer] Total tokens: {len(all_tokens)}")

    # 3. 分割训练/验证
    split = int(len(all_tokens) * 0.9)
    train_tokens = all_tokens[:split]
    val_tokens = all_tokens[split:]

    train_ds = CapsuleDataset(train_tokens, BLOCK_SIZE)
    val_ds = CapsuleDataset(val_tokens, BLOCK_SIZE)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

    # 4. 模型
    model = AmberGPT(vocab_size=len(tokenizer.vocab))
    model.to(device)
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=WEIGHT_DECAY)

    print(f"[trainer] Model params: {sum(p.numel() for p in model.parameters()):,}")

    # 5. 训练循环
    model.train()
    start_time = time.time()
    best_val_loss = float("inf")

    iter_count = 0
    final_loss = 0.0

    while iter_count < iterations:
        epoch_start = time.time()
        for xb, yb in train_loader:
            if time.time() - start_time > 300:  # 5分钟 wall clock 保护
                break

            xb, yb = xb.to(device), yb.to(device)
            logits, loss = model(xb, yb)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            iter_count += 1
            if progress_callback:
                progress_callback(iter_count, loss.item())

            if iter_count >= iterations:
                break

        # 验证
        model.eval()
        val_loss = 0.0
        count = 0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                _, loss = model(xb, yb)
                val_loss += loss.item()
                count += 1
        val_loss /= max(count, 1)
        model.train()

        elapsed = time.time() - epoch_start
        bpb = val_loss / math.log(2)
        print(f"[trainer] iter {iter_count:4d} | val_loss: {val_loss:.4f} | val_bpb: {bpb:.4f} | elapsed: {elapsed:.1f}s")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            # 保存 best
            torch.save({
                "model_state": model.state_dict(),
                "vocab_size": len(tokenizer.vocab),
                "iterations": iter_count,
                "val_loss": best_val_loss,
            }, MODEL_PATH)

        final_loss = val_loss

    elapsed = time.time() - start_time
    print(f"[trainer] Training complete in {elapsed:.1f}s, best val_loss: {best_val_loss:.4f}")

    return {
        "status": "ok",
        "iterations": iter_count,
        "final_loss": final_loss,
        "val_bpb": final_loss / math.log(2),
        "model_path": str(MODEL_PATH),
        "tokenizer_path": str(TOKENIZER_PATH),
        "device": device,
    }


def _load_all_capsule_texts() -> list[str]:
    """加载所有胶囊的文本内容用于 tokenizer 构建"""
    import sqlite3
    from pathlib import Path
    texts = []
    try:
        DB_PATH = Path.home() / ".amber-hunter" / "hunter.db"
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        # 按 created_at 分批加载（created_at 是浮点数时间戳，适合 > 比较）
        last_ts: float | None = None
        batch_size = 300
        while True:
            if last_ts is not None:
                rows = c.execute(
                    "SELECT memo, content, tags, created_at FROM capsules WHERE created_at < ? ORDER BY created_at DESC LIMIT ?",
                    (last_ts, batch_size)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT memo, content, tags, created_at FROM capsules ORDER BY created_at DESC LIMIT ?",
                    (batch_size,)
                ).fetchall()
            if not rows:
                break
            for memo, content, tags, created_at in rows:
                if memo:
                    texts.append(memo)
                if content:
                    texts.append(content[:500])
                if tags:
                    texts.append(tags)
            last_ts = rows[-1][3] if rows else None  # created_at is index 3
            if len(rows) < batch_size:
                break
        conn.close()
    except Exception as e:
        print(f"[trainer] Failed to load capsules: {e}")
    return texts


# ── 推理 API ─────────────────────────────────────────────────

class AmberTrainer:
    """
    加载训练好的模型，提供推理 API。
    用法：
        at = AmberTrainer()
        score = at.score(query="test query", memory_text="a test memory")
        tags = at.predict_tags("this is a memory about python programming")
    """
    _instance: Optional["AmberTrainer"] = None

    def __init__(self):
        self.model: AmberGPT | None = None
        self.tokenizer: SimpleTokenizer | None = None
        self.device: str = "cpu"
        self._load()

    def _load(self):
        if not MODEL_PATH.exists() or not TOKENIZER_PATH.exists():
            return
        try:
            self.tokenizer = SimpleTokenizer.load(TOKENIZER_PATH)
            ckpt = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
            self.model = AmberGPT(vocab_size=ckpt["vocab_size"])
            self.model.load_state_dict(ckpt["model_state"])
            if torch.backends.mps.is_available():
                self.device = "mps"
                self.model.to("mps")
            print(f"[trainer] Loaded model from {MODEL_PATH}")
        except Exception as e:
            print(f"[trainer] Failed to load model: {e}")
            self.model = None

    def is_ready(self) -> bool:
        return self.model is not None and self.tokenizer is not None

    @torch.no_grad()
    def score(self, query: str, memory_text: str) -> float:
        """
        给定 query 和 memory 文本，返回相关度分数 [0, 1]。
        使用 query 和 memory 的 [CLS] 表示做点积。
        """
        if not self.is_ready():
            return 0.5  # 未训练时返回中性分数

        query_tokens = self.tokenizer.encode(query)[:BLOCK_SIZE // 2]
        mem_tokens = self.tokenizer.encode(memory_text)[:BLOCK_SIZE // 2]

        # 构造 [query] [SEP] [memory] 序列
        sep_id = self.tokenizer.stoi.get("<EOS>", 1)
        combined = query_tokens + [sep_id] + mem_tokens

        q_emb = self.model.encode(query_tokens)
        m_emb = self.model.encode(mem_tokens)

        # 余弦相似度
        sim = torch.nn.functional.cosine_similarity(
            q_emb.unsqueeze(0), m_emb.unsqueeze(0)
        ).item()
        return float(max(0.0, min(1.0, sim)))

    @torch.no_grad()
    def rerank(self, query: str, memory_texts: list[str], top_k: int = 5) -> list[tuple[int, float]]:
        """
        对 memory 列表 rerank，返回 [(index, score), ...] 排序结果。
        """
        if not self.is_ready():
            return [(i, 0.5) for i in range(len(memory_texts))]

        scores = [self.score(query, mem) for mem in memory_texts]
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])[:top_k]
        return ranked

    @torch.no_grad()
    def predict_tags(self, text: str, top_k: int = 3) -> list[tuple[str, float]]:
        """
        预测 text 的标签，返回 [(tag, score), ...]。
        基于记忆中的标签分布和文本匹配。
        """
        if not self.is_ready():
            return []  # fallback 到规则化标签

        tokens = self.tokenizer.encode(text)[:BLOCK_SIZE]
        emb = self.model.encode(tokens)

        # 简单实现：使用 hidden state 的前几个 dimension 做聚类
        # 实际应用中应该用 labeled data 训练一个分类头
        # 这里返回一个 placeholder——训练好后可以扩展
        return []  # 暂时返回空，等有标注数据后实现分类头

    @torch.no_grad()
    def extract_memories(self, conversation: str, max_new_tokens: int = 50) -> list[str]:
        """
        给定对话文本，用模型生成记忆片段。
        自回归生成，类似于摘要抽取。
        """
        if not self.is_ready():
            return []

        primer = self.tokenizer.encode(conversation[:BLOCK_SIZE])
        generated_ids = self.model.generate(primer, max_new=max_new_tokens)
        generated_text = self.tokenizer.decode(generated_ids[len(primer):])
        # 按句子分割
        import re
        sentences = re.split(r'[。.!]+', generated_text)
        return [s.strip() for s in sentences if len(s.strip()) > 10]


# ── 单例访问 ─────────────────────────────────────────────────

def get_trainer() -> AmberTrainer:
    if AmberTrainer._instance is None:
        AmberTrainer._instance = AmberTrainer()
    return AmberTrainer._instance


def is_trained() -> bool:
    """检查是否有已训练的模型"""
    return MODEL_PATH.exists() and TOKENIZER_PATH.exists()


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AmberGPT Fine-tune & Inference")
    parser.add_argument("action", choices=["train", "score", "rerank", "status"])
    parser.add_argument("--query", type=str, default="")
    parser.add_argument("--memory", type=str, default="")
    args = parser.parse_args()

    if args.action == "train":
        print(f"[trainer] Starting fine-tune on {MODEL_DIR} ...")
        result = fine_tune()
        print(json.dumps(result, indent=2))

    elif args.action == "score":
        at = get_trainer()
        if not at.is_ready():
            print("Model not trained yet. Run: python -m core.trainer train")
            sys.exit(1)
        s = at.score(args.query, args.memory)
        print(f"Relevance score: {s:.4f}")

    elif args.action == "rerank":
        at = get_trainer()
        if not at.is_ready():
            print("Model not trained yet.")
            sys.exit(1)
        memories = args.memory.split("|")
        ranked = at.rerank(args.query, memories)
        for idx, score in ranked:
            print(f"[{score:.3f}] {memories[idx][:80]}")

    elif args.action == "status":
        if is_trained():
            ckpt = torch.load(MODEL_PATH, map_location="cpu", weights_only=False)
            print(f"Model: {MODEL_PATH}")
            print(f"  trained iterations: {ckpt.get('iterations', '?')}")
            print(f"  val_loss: {ckpt.get('val_loss', '?'):.4f}")
            print(f"  vocab_size: {ckpt.get('vocab_size', '?')}")
        else:
            print("No trained model yet. Run: python -m core.trainer train")
