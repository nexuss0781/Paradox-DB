#!/bin/bash
set -euo pipefail

# Paradox-DB CLI Installer
# Usage: curl -sSL https://get.paradox-db.dev | bash

REPO="nexuss0781/Paradox-DB"
INSTALL_DIR="${HOME}/.local/bin"
BINARY_NAME="tgdb"

echo "Installing Paradox-DB CLI..."

# Detect platform
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Linux*)  PLATFORM="linux" ;;
    Darwin*) PLATFORM="macos" ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
    *) echo "Unsupported OS: $OS"; exit 1 ;;
esac

case "$ARCH" in
    x86_64|amd64) ARCH_NAME="x64" ;;
    aarch64|arm64) ARCH_NAME="arm64" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

echo "Detected: $PLATFORM-$ARCH_NAME"

# Create install directory
mkdir -p "$INSTALL_DIR"

# Check for bun or node
if command -v bun &>/dev/null; then
    RUNTIME="bun"
elif command -v node &>/dev/null; then
    RUNTIME="node"
else
    echo "Error: bun or node.js is required"
    echo "Install bun: https://bun.sh"
    echo "Install node: https://nodejs.org"
    exit 1
fi

echo "Using runtime: $RUNTIME"

# Clone or update repo
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

echo "Downloading Paradox-DB..."
if command -v git &>/dev/null; then
    git clone --depth 1 "https://github.com/$REPO.git" "$TEMP_DIR/paradox-db"
else
    echo "Error: git is required for installation"
    exit 1
fi

cd "$TEMP_DIR/paradox-db/client"

# Install dependencies
if [ "$RUNTIME" = "bun" ]; then
    bun install
    bun run build
else
    npm install
    npm run build
fi

# Create wrapper script
cat > "$INSTALL_DIR/$BINARY_NAME" << 'WRAPPER'
#!/bin/bash
exec node "$HOME/.paradox/paradox-cli/dist/cli.js" "$@"
WRAPPER

chmod +x "$INSTALL_DIR/$BINARY_NAME"

# Also copy the built CLI directly
mkdir -p "$HOME/.paradox/paradox-cli"
cp -r dist "$HOME/.paradox/paradox-cli/"
cp package.json "$HOME/.paradox/paradox-cli/"

echo ""
echo "Paradox-DB installed successfully!"
echo ""
echo "Binary: $INSTALL_DIR/$BINARY_NAME"
echo ""
echo "Add to PATH if needed:"
echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
echo ""
echo "Get started:"
echo "  tgdb --help"
echo "  tgdb init mydb"
