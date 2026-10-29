## {{PROJECT_NAME}} 执行约定

Codex 管项目、Git、CLI、工作树与验收。收到 HANDOFF.md 后，只执行其指定目录、分支和允许路径内的任务；只读指定片段和必要局部范围。不要扫描全仓库、另开代理、修改工单或宿主记录、执行命令或扩大权限。必要项目规则由负责人提炼进交接。

缺上下文返回 BLOCKED，给出路径、行范围和原因。返回 JSON，首项 status=COMPLETED|BLOCKED，随后短 summary、acceptance（每个 AC 一条 id/status/evidence）和 risks。宿主检查未执行时如实说明，不自行给 GPT PASS、提交、推送、合并或部署。同单修订继续原会话。
