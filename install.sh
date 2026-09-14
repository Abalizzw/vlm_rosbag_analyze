#!/bin/bash

echo "=== Create Python Virtual Environment ==="

python3 -m venv vlm_env

source vlm_env/bin/activate

echo "=== Upgrade Pip ==="

pip install --upgrade pip

echo "=== Install Python Packages ==="

pip install -r requirements.txt

echo ""
echo "======================================="
echo "Installation Complete"
echo "======================================="