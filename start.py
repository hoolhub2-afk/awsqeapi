#!/usr/bin/env python
"""一键启动脚本"""
import os
import sys
import subprocess

def main():
    # 切换到脚本所在目录
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # 从 .env 读取端口配置
    port = "8000"
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PORT="):
                    port = line.split("=", 1)[1].strip().strip('"')
                    break

    cmd = [
        sys.executable, "-m", "uvicorn",
        "app:app",
        "--host", "0.0.0.0",
        "--port", port,
        "--reload",
        "--access-log"
    ]

    print(f"Starting server on http://0.0.0.0:{port}")
    subprocess.run(cmd)

if __name__ == "__main__":
    main()
