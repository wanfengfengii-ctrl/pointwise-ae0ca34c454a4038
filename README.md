# 澄川复测分析归档

Python 3.11标准库离线工具。站点实验室提交检测与更正修订，校准实验室提交共享误差模型；分析员固定输入、生成评分与概率报告，复测协调员读取和交付结果。

## 一天的工作

1. 接纳完整检测批次，回执确认明确修订。
2. 选择2至8个不同站点的具体修订，固定有序队列。
3. 保存校准修订，固定共享U/V、情景、权重和评分规则。
4. 将固定观测代入规则，归档共享空间中的仿射评分。
5. 提交名额k和至多两个有序目标站点。
6. 积分同一个排名事件，得到边缘、共同及至少一个入选概率。
7. 原子发布报告，协调员按ID读取结果和完整来源。
8. 导出自足包，在没有原数据库时显式复算；更正生成新修订和新成果。

## 运行

在项目根目录执行，无需安装依赖。python3应指向Python 3.11。

    python3 -B -m unittest discover -s tests
    python3 -B -m monitor.analysis.jobs demo --manifest run_manifest.json

demo在系统临时目录创建数据库和交付包，实际执行接纳、固定、评分、分析、读取、导出和离线复算，核对手算结果后清理。输出实际报告和POINTWISE_SCENARIO JSON。

持久运行可使用自行准备的输出目录，建议位于源码之外：

    python3 -B -m monitor.analysis.jobs run --manifest run_manifest.json --db /tmp/chengchuan.sqlite --out /tmp/chengchuan-report.json
    python3 -B -m monitor.archive.delivery verify /tmp/chengchuan-report.json

再次导出时，将下列REPORT_ID替换为第一条命令输出的ids.report：

    python3 -B -m monitor.archive.delivery export --db /tmp/chengchuan.sqlite --report REPORT_ID --out /tmp/chengchuan-copy.json

CLI业务错误输出error JSON到stderr并退出2；unittest保留正常失败退出码。初始化不清空数据库。相同示例重复运行复用回执、队列、模型和请求成果，数量不增长。修改检测内容须使用新批次、新修订及新请求身份；校准更正须使用新校准修订。同身份异内容拒绝。

## Python公开接口

    from monitor.archive.database import ArchiveDatabase
    from monitor.observations.revisions import ingest
    from monitor.observations.cohorts import freeze_cohort
    from monitor.calibration.models import save_calibration
    from monitor.scoring.jobs import score
    from monitor.analysis.jobs import analyze
    from monitor.archive.reading import read_result
    from monitor.archive.delivery import export_result, verify_bundle

ArchiveDatabase(path)支持上下文管理；省略path使用内存库。

- ingest(db, batch, rows)返回不可变IngestReceipt，含id、batch和有序修订成果ID。
- freeze_cohort(db, references)返回队列ID，引用为(source, station, revision)。
- save_calibration(db, raw)返回模型ID。
- score(db, request_id, cohort_id, model_id)返回评分ID。
- analyze(db, request_id, score_id, k, targets)返回报告ID。
- read_result(db, id)返回独立字典，含id、kind、payload及以ID索引的lineage；报告payload含request、marginals、joint、union和parents，概率为Fraction。
- export_result(db, report_id, path)原子替换交付目标。
- verify_bundle(text_or_decoded_bundle)显式复算，返回含Fraction的报告值，不访问原数据库。

run_manifest.json提供完整有限样例。领域值对象、纯计算函数和装载函数供同一流程复用。低层publish_artifact、修订登记及回执写入须由上层事务调用，不独立提前提交。

## 输入与计算

公开JSON有理输入只接受整数或p/q字符串，分母可带符号但不得为零；不接受布尔、浮点、空值或缺字段。内部使用Fraction。身份为非空不透明字符串，不重写大小写或字符；列表顺序有意义。

观测字段恰为source、station、revision、x、r_u、r_v、unit。unit为ug/m3；x允许零点修正后的负值，r_u/r_v为每单位无量纲共同坐标的浓度响应。修订键为(source,station,revision)。批内重复键拒绝；已存同键同内容复用，异内容使整批拒绝，包括冲突前后的合法新记录。

校准字段恰为source、revision、variables、scenarios、rule；variables为["U","V"]。rule含delta、alpha、beta、gamma、version=affine-v1、input_unit=ug/m3、output_unit=score。评分为a+bU+cV，其中a=delta+alpha*x、b=beta*r_u、c=gamma*r_v；分数越高越优先。各站共享同一对U/V，完整保留响应符号与原坐标。

情景数1至4，非负权重精确和为1。点情景字段为kind=point、weight、u、v；矩形为kind=rectangle、weight、u_lo、u_hi、v_lo、v_hi，两轴长度均须为正。零权情景仍校验；重叠情景分别按自身条件测度及权重计量。

k为1至站点数的整数。严格高于某站的站点数少于k即入选，并列边界全部入选，人数可超过k。矩形按精确半平面裁剪面积积分，恒等评分及点上的并列完整保留。共同概率在同一情景中判断，并集满足P(A或B)=P(A)+P(B)-P(A且B)。目标至多两个、互异，按身份解析并按请求顺序输出。单目标的joint和union等于边缘；空目标返回[]、0、0。

## 固定、错误及交付

observations负责明确修订和有序队列；calibration负责不可变模型；scoring负责共享评分交接；analysis负责严格排名和混合测度；archive负责目录、事务、读取和交付。24个生产模块均接入业务流程。

队列保存明确修订及来源快照，评分固定队列和模型，报告固定评分、名额和有序目标。更正、返回对象修改及数据库重开不改旧成果。读取与导出只检查固定数据及来源，不评分或积分；未知、未提交及损坏来源明确报错。摘要用于完整性核对，不是身份认证。

score和analyze请求命名空间独立。同身份同规范化输入复用，异输入抛RequestConflictError。载荷、父引用和请求绑定共同事务提交，失败回滚且可稳定重试。db.fault(stage)支持payload、parents、binding阶段故障演练；export_result的fault参数支持write、before_replace演练。撤除演练钩子后正常运行。

观测、队列、校准、评分、分析错误分别为ObservationError、CohortError、CalibrationError、ScoringError、AnalysisError，继承ValueError。存储错误为StorageError及PublicationError/RequestConflictError子类，读取错误为ReadError，交付错误为DeliveryError，均继承RuntimeError。数据库事务原样传播注入的非SQLite异常；交付写出异常包装为DeliveryError。非法输入不发布成果。

交付包版本1包含root、全部有类型父成果、精确载荷及source_digest，不依赖原机器路径。有理值编码为{"$fraction":[分子,正分母]}，顺序保留。verify_bundle核对闭包、类型、摘要和来源，再由原观测、规则及情景复算评分和报告。导出在目标同目录写临时文件，完成关闭后原子替换；替换前失败保留旧文件并清理临时内容。

## 确定性回归

8个测试文件覆盖：对向评分边缘各1/2、共同0、并集1；矩形和零点各半得到3/4、3/4、1/2、1；情景拆分；完整并列；非对称身份运输；非单位矩形和二维独立面积核账；请求与规则边界；批次冲突和精确重放；观测及校准更正；独立值副本、初始化保全和重开禁止重算；发布回滚、请求冲突和损坏读取；交付故障保全、脱离原库复算、伪造结果拒绝和示例重复执行。

相同规范化输入、修订、队列顺序、模型、k和目标顺序产生相同内容ID与精确结果，不使用随机数或时间戳参与领域身份。
