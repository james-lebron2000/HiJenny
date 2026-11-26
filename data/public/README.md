# HiJenny 公开资产动态

公开演示名单从 Summit Therapeutics 与 ivonescimab 的 NCT05899608、NCT07264075 开始。每日台北时间 08:17 计划检查，也可在 GitHub Actions 手动运行 Public asset events。公开文件只收录结构化事实和官方链接。SEC 申报索引从名单的 since 日期开始，试验字段变化从第一次观察起计算，轮询之间未观察到的版本无法重建。首次收录不表示刚发生。

月文件按 UTC 观察月份归档。source_date 是本条所引来源的披露或登记更新日，observed 为首次观察时间，effective 保留已核实事件日期的原始精度。reviewed 记录经过原文核对；SEC 1.01 和 1.02 只提示检查可能相关的协议。试验完成日期的 ESTIMATED/ACTUAL 类型随日期一起保存。来源失败时工作流标红，成功来源仍可提交，下一次重新核对。相同事实不重复提交。

服务器通过 hijenny-public-events.timer 每小时拉取 GitHub main 的月文件，按 Git blob 哈希验证内容，再事务写入单独的 public-events.db。查询入口为 #/public-events，接口为 /api/public-events，可传 asset、region、since、offset 与 limit。首次部署可用 python -m hijenny.public_events --local 导入随版本发布的基线。若定时运行被平台停用，维护者可在 Actions 中重新启用。

客户专属名单放在仓库之外的授权项目目录中。服务器运行 tools/collect_public_events.py --private-dir 目录时，只读该目录的 watchlist.json，结果写入同一目录的 events；这些结果不参与公开索引同步。客户文件、业务判断、凭据和原始全文均不属于此仓库数据集。

授权项目需要定时采集时，在服务器以服务账户创建权限为 0700 的 ~/.local/share/hijenny/private-watchlists/项目代号，将名单以 0600 权限保存为 watchlist.json；安装 deploy 下对应的模板后，启用 hijenny-private-collect@项目代号.timer。该实例每日台北时间 08:47 检查，日志与输出只留在服务器。没有授权项目时无需启用实例。

PubMed 按 publications 名单中的 NCT 编号检索，同时检索数据库关联号与题名或摘要。优先核对文献元数据中 ClinicalTrials.gov 数据库的登记号；仅题名或摘要出现完整编号时按提及位置归档，需复核其与试验结果的关系。题名、PMID 与 PubMed 收录日期写入公开索引；是否报告疗效结果需另行复核。

已匹配论文逐次比较 PubMed 的更正、撤稿、重刊和编辑关注标记，保留原文与通知的关系方向。元数据修订日期只表示记录修订时间。状态的首条观察构成基线，可以与后续字段变化分别筛选。

Drugs@FDA 按公开名单中的 NDA、ANDA 或 BLA 申请号核对原始与补充申请，收录状态为 AP 的批准决定。每条保留申请类型、序号、决定日与 FDA 申请详情入口。补充申请的分类取自原记录，标签更新的具体范围须核对相应文件。

每次 GitHub 采集运行的摘要逐项目列出来源、核对状态、不同来源记录编号数与新增事件数。PubMed 无匹配时记录数为 0，失败时数量为未知。下载该次运行的 public-collection-receipt 可获得 UTC 起止时间和规范化关注名单的 SHA-256；采集回执与资产事件月文件分别保存。

最新公开回执还写入 events/collection.json，与月文件一同同步至服务器。在「公开资产动态」展开「最近一次公开采集」可查看整份名单的核对时间、成功数及逐项结果；详情链接指向对应的 GitHub 运行。

逐项回执保留名单中的资产或公司，以及该采集目标最近成功核对的时间。连续失败时沿用同一来源、编号和名单配置的最后成功时间；配置发生变化后，重新建立该目标的核对记录。

欧盟监管数据取自 [EMA 人用药集中程序数据](https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format)。按 EMA 产品编号匹配名单并保存原产品页链接。页面更新日、上市许可日期、欧委会决定日期和首次观察时间分别保存。最近程序编号用于回查产品信息修订范围。

日本试验按 jRCT 编号核对公开英文登记页，收录招募状态、登记日期、申办方和登记所列 NCT 编号。关联号及发号机构须与来源相符。登记更新日与 HiJenny 首次观察时间分别保存。

「公开资产动态」支持输入完整 jRCT 或 NCT 编号查看同一试验已归档的跨来源记录。以 jRCT 编号查看历史截面时，只采用当时已经披露并已收录的登记关联。

美国专利来源采用 [FDA Orange Book 月度下载文件](https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files)。按申请及产品编号对照产品、专利与独占期三张表；同一专利的不同用途代码各自保留。来源日期取下载文件的最后修改日。
