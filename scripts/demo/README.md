# 演示录屏

`demo_script.py` 用 Playwright 打开 TraceMark，视口 1280×800，在页面顶部放中文字幕条，并录下：

1. 平台概览
2. 多模态拆分打标。页面不再单独校验，复核入口跳到合规检测
3. 虚拟场景和交互场景的写入
4. 合规检测依次上传：正常的多模态 MP4、带重复标识的多模态 MP4、虚拟场景包、交互场景包
5. 对重复标识的 MP4 生成修复方案、确认执行、下载并复检

脚本会自己生成 5 秒的演示 MP4 和交互场景文本。三个服务需要已经在 8000、8002、5173 监听。

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
python3 scripts/demo/demo_script.py --base http://127.0.0.1:5173 --out /tmp/marktool-demo
```

输出是 `/tmp/marktool-demo/marktool-demo.mp4`（H.264，无音轨）和 `timeline.json`。
