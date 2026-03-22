#!/bin/bash
# release.sh — Create GitHub Release with CHANGELOG entry
# Usage: bash release.sh [version] [github_token]
# Example: bash release.sh v0.9.0 ghp_xxx...

set -e

VERSION=${1:?Usage: bash release.sh v0.9.0 [github_token]}
TOKEN=${2:-${GITHUB_TOKEN:?Requires GITHUB_TOKEN env or second argument}}
REPO="ankechenlab-node/amber_hunter"
TAG="v${VERSION#v}"

echo "🌙 Creating release $TAG for $REPO"

# Extract changelog section for this version
SECTION=$(awk "/## \[$TAG\]/,/^##/" CHANGELOG.md 2>/dev/null | sed '1d' | head -50)
if [ -z "$SECTION" ]; then
    echo "⚠️  No CHANGELOG entry found for $TAG, using generic body"
    BODY="Release $TAG of amber-hunter."
else
    BODY="$SECTION"
fi

# Create tag if not exists
if ! git rev-parse "$TAG" >/dev/null 2>&1; then
    git tag -a "$TAG" -m "Release $TAG"
    git push origin "$TAG"
    echo "  → Tag $TAG created and pushed"
else
    echo "  → Tag $TAG already exists"
fi

# Create GitHub release
RESPONSE=$(curl -s -X POST \
    "https://api.github.com/repos/$REPO/releases" \
    -H "Authorization: token $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$(jq -n \
        --arg tag "$TAG" \
        --arg name "amber-hunter $TAG" \
        --arg body "$BODY" \
        '{tag_name: $tag, name: $name, body: $body, draft: false}' \
    )")

URL=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('html_url', d.get('message','error')[:80]))")
echo "  → Release: $URL"
