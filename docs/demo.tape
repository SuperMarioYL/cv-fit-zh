Output assets/demo.gif
Set FontSize 15
Set Width 1200
Set Height 640
Set Padding 16
Set Theme "Dracula"

# cv-fit-zh — 30 秒把旧简历按 JD 改写成 ATS 友好的中文简历。
# 本录屏走 CLI 离线路径（无需 API key），同样适用于 Web UI。

Type "echo '## 1. 解析 JD → 结构化技能关键词'"
Sleep 200ms
Enter
Sleep 400ms

Type "python -m app.jd.parser --jd examples/sample.jd.txt"
Sleep 200ms
Enter
Sleep 3s

Type "echo '## 2. 旧简历 + JD → STAR 改写 + 关键词覆盖分'"
Sleep 200ms
Enter
Sleep 400ms

Type "python -m app.resume.star --resume examples/sample.resume.txt --jd examples/sample.jd.txt"
Sleep 200ms
Enter
Sleep 5s

Type "echo '## 3. 起 Web UI: uvicorn app.main:app → localhost:8000'"
Sleep 200ms
Enter
Sleep 1s

Type "echo '   粘贴旧简历 + JD → 30s 下载中文简历 PDF（右侧附覆盖分）'"
Sleep 200ms
Enter
Sleep 1s
