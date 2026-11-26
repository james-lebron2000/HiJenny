# HiJenny 公开资产动态

公开演示名单从 Summit Therapeutics 与 ivonescimab 的 NCT05899608、NCT07264075 开始。每日台北时间 08:17 计划检查，也可在 GitHub Actions 手动运行 Public asset events。公开文件只收录结构化事实和官方链接。SEC 申报索引从名单的 since 日期开始，试验字段变化从第一次观察起计算，轮询之间未观察到的版本无法重建。首次收录不表示刚发生。

月文件按 UTC 观察月份归档。source_date 是本条所引来源的披露或登记更新日，observed 为首次观察时间，effective 保留已核实事件日期的原始精度。reviewed 记录经过原文核对；SEC 1.01 和 1.02 只提示检查可能相关的协议。试验完成日期的 ESTIMATED/ACTUAL 类型随日期一起保存。来源失败时工作流标红，成功来源仍可提交，下一次重新核对。相同事实不重复提交。

服务器通过 hijenny-public-events.timer 每小时拉取 GitHub main 的月文件，按 Git blob 哈希验证内容，再事务写入单独的 public-events.db。查询入口为 #/public-events，接口为 /api/public-events，可传 asset、region、since、offset 与 limit。首次部署可用 python -m hijenny.public_events --local 导入随版本发布的基线。若定时运行被平台停用，维护者可在 Actions 中重新启用。

客户专属名单放在仓库之外的授权项目目录中。服务器运行 tools/collect_public_events.py --private-dir 目录时，只读该目录的 watchlist.json，结果写入同一目录的 events；这些结果不参与公开索引同步。客户文件、业务判断、凭据和原始全文均不属于此仓库数据集。
