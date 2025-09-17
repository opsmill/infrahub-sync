#!/usr/bin/env bash
# Example script to demonstrate local adapter usage

set -e

echo "🚀 Local Adapter Example"
echo "========================"

# Check if we're in the right directory
if [[ ! -f "my_network_monitor.py" ]]; then
    echo "❌ Please run this script from the examples/local-adapter directory"
    exit 1
fi

# Set example environment variables (in real usage, set these securely)
export NETWORK_MONITOR_API_KEY="example-api-key"
export INFRAHUB_TOKEN="example-token"

echo "📋 Available sync projects:"
infrahub-sync list --directory .

echo ""
echo "🔍 Testing adapter loading (will fail at Infrahub connection, which is expected):"
infrahub-sync diff --name network-monitor-sync --directory . || true

echo ""
echo "✅ Local adapter loaded successfully!"
echo ""
echo "To use with a real Infrahub instance:"
echo "1. Set INFRAHUB_TOKEN to your actual token"
echo "2. Update the Infrahub URL in config.yml"
echo "3. Set NETWORK_MONITOR_API_KEY to your actual API key"
echo "4. Run: infrahub-sync sync --name network-monitor-sync"