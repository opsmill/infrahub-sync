#!/usr/bin/env bash
# Demo script showing different plugin loader capabilities

set -e

echo "🚀 Plugin Loader System Demo"
echo "============================"

# Check if we're in the right directory
if [[ ! -f "config.yml" ]]; then
    echo "❌ Please run this script from the examples/plugin-loader-demo directory"
    exit 1
fi

echo "📂 Example directory structure:"
echo "$(tree -a || find . -type f | sort)"

echo ""
echo "📋 Available sync configurations:"
infrahub-sync list --directory .

echo ""
echo "🔧 Testing plugin resolution from local adapters directory:"
echo "   Configuration uses: adapter: 'my_network'"
echo "   Should resolve to: ./adapters/my_network.py:MyNetworkAdapter"

echo ""
echo "🧪 Testing different resolution methods:"

echo ""
echo "1️⃣  Built-in adapter (backward compatible):"
echo "   Uses: name: 'infrahub' → infrahub_sync.adapters.infrahub:InfrahubAdapter"

echo ""
echo "2️⃣  Local file with search path:"
echo "   Uses: adapter: 'my_network' + adapters_path: ['./adapters']"
echo "   → ./adapters/my_network.py:MyNetworkAdapter"

echo ""
echo "3️⃣  Explicit file path:"
echo "   Uses: adapter: './adapters/my_network.py:MyNetworkAdapter'"

echo ""
echo "4️⃣  Dotted import:"
echo "   Uses: adapter: 'adapters.my_network:MyNetworkAdapter'"

echo ""
echo "💡 Environment variable support:"
echo "   Set INFRAHUB_SYNC_ADAPTER_PATHS='./adapters:../shared'"

echo ""
echo "🎯 CLI adapter paths:"
echo "   Use --adapter-path ./adapters --adapter-path ../shared"

echo ""
echo "✅ Plugin loader system provides maximum flexibility while"
echo "   maintaining full backward compatibility!"

echo ""
echo "To test with a real Infrahub instance:"
echo "1. Set INFRAHUB_TOKEN environment variable"
echo "2. Update Infrahub URL in config.yml" 
echo "3. Run: infrahub-sync diff --name plugin-loader-demo"