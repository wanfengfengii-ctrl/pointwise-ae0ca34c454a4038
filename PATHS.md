# 有限浓度修正的复测概率曲线

## 已有流程与新增结果

README.md 的 V1 为正常可运行基线：观测修订、有序队列、共享校准模型、静态仿射评分、单次排名概率、固定成果与自足交付均已实现。本题只增加同一固定评分的有限浓度修正路径及其完整概率曲线。所有原文件、接口、错误边界和原回归继续有效；新入口在初始树中尚未实现。path_examples.py 是公开调用与手算夹具，没有曲线算法。

评分职责拥有站点坐标、浓度方向及参数化系数，排名分析职责只消费已保存路径；交付职责保存并复算同一父链。只发布方向不能获得概率曲线；只在分析时读取当前观测或临时传入方向，无法让旧路径在更正后仍有同一解释。需将新增语义接入现有评分表示/编译及排名事件/测度职责，而非循环发布单点任务。

## Python 入口与数据

新增公开入口：

    from monitor.scoring.jobs import score_path
    from monitor.analysis.jobs import analyze_path

    path_id = score_path(db, request_id, score_id, directions, lo, hi)
    curve_id = analyze_path(db, request_id, path_id, k, targets)

两个入口返回成果ID字符串。score_id 必须是已经提交的 V1 score；路径不能以另一路径为父。directions 是字典，键恰为父评分全部站点身份，每站一个方向；按站点身份绑定，字典插入顺序不影响规范化输入。lo、hi、各方向沿用 V1 有理输入规则（整数、p/q 字符串，Python 已解码的 Fraction 亦可），不得用布尔或浮点；lo<=hi，两端有限。零方向合法，不修改父观测或生成观测更正。

对父评分第 i 站，用 x_i(t)=x_i+t*d_i，仅改变浓度，响应和固定校准规则不变。参数 t 是所有站点共同的同一个数；每个情景仍使用同一对共享 U/V。站点数、情景数、k、targets、有理精度与 V1 相同：2至8站、1至4情景、1<=k<=站点数、至多两个互异有序目标。空目标仍校验全部输入，概率为[]、0、0；单目标的共同及并集等于边缘。严格超过者不足 k 即入选，所有边界并列完整保留。零权情景仍校验，重叠情景分别计量。

评分路径使用 kind=score_path。read_result(db,path_id)["payload"] 的公开字段恰为：
- score_id：原父评分ID。
- stations：原评分顺序的站点列表。
- directions：与 stations 对齐的 Fraction 列表。
- coefficients：同序四项 Fraction 列表的列表，每行 [a,b,c,e] 表示 a+b*U+c*V+e*t。它必须与父观测、固定规则及方向相符。
- lo、hi：Fraction。
- parents：{"score": score_id}。

analyze_path 只能消费该固定路径和其来源校准模型，不接受另传方向或当前修订替代。概率曲线使用 kind=path_report。read_result(db,curve_id)["payload"] 的公开字段恰为：
- request：{"path_id": path_id, "k": k, "targets": 有序站点列表}。
- breakpoints：从 lo 到 hi 的严格递增 Fraction 列表。
- pieces：长度 len(breakpoints)-1。第 j 项适用于 (breakpoints[j],breakpoints[j+1])。
- values：与 breakpoints 等长，第 j 项是该参数处的实际概率，不是相邻极限。
- parents：{"path": path_id}。

每个 pieces 项恰为 {"marginals": [[c0,c1,c2],...], "joint": [c0,c1,c2], "union": [c0,c1,c2]}。系数全为 Fraction，按 t 的升幂，低次式补零至三项；marginals 按目标顺序。每个 values 项恰为 {"marginals": [Fraction,...], "joint": Fraction, "union": Fraction}。lo=hi 时 breakpoints=[lo]、pieces=[]、values 仅一项。

曲线须在整个闭区间精确正确。不能用网格、端点外推或未经完整分界依据的插值代替完整曲线；允许全量重算及复用 V1 单点积分。矩形部分可在开段为二次以内多项式；点情景可跳变、出现孤立值，空间恒等评分在特定参数处也可能使矩形产生孤立并列质量。内部断点当且仅当左右所有概率的系数向量相同、且该点实际向量等于该多项式的值时删除，反复合并至不可删除；两个区间端点总保留。不能把单点异常合进相邻段。返回独立值副本。

## 发布、冻结和自足交付

新请求命名空间分别为 score_path、analyze_path，独立于彼此及 V1 命名空间。路径规范化输入为父评分、按原站点排列的方向、lo、hi；曲线规范化输入为 path_id、k、有序 targets。同身份同规范化输入复用，异输入抛 RequestConflictError；新请求相同内容沿用内容ID语义。未知或错误父类型、方向缺项/多项/非法、倒置区间等抛 ScoringError；非法分析请求或未知/错误路径抛 AnalysisError。沿用原读取损坏、存储和交付错误类型。非法输入不发布，失败回滚且撤除故障后可按同请求重试。

路径父链为 score_path -> score -> cohort/model，曲线父链为 path_report -> score_path。新载荷、类型化父引用和请求绑定共同发布，复用 db.fault 的 payload、parents、binding 钩子。后续观测/校准更正、修改 directions 或读取结果、关闭重开都不改变已发布路径与曲线。

read_result 和 export_result 读取固定结果及来源，不重新编译路径或计算曲线。export_result 新增支持 path_report 根，沿用同目录临时文件、原子替换、write/before_replace 故障和旧目标保全；不要求导出 score_path 根。V1 包版本1及其读取复算继续兼容；新曲线包也使用版本1的精确编码与闭包格式，包含全部固定来源且不依赖原数据库。verify_bundle 显式核验父链、评分与修正系数，独立重算完整曲线并比对；不能仅信任已存系数或摘要。新曲线复算返回 request、breakpoints、pieces、values 四项，不含 parents，与 V1 复算返回值习惯一致。导出只读，显式复算才进行计算。

## 手算及交付演示

path_examples.py 的三个有限例是公开普通核验：
- mixed：A=U+t、B=-U-t、C=0；[-1,1]^2 矩形和零点各半，k=1，目标 A/B，t∈[-1,1]。负段边缘为 (1+t)/4、(3-t)/4，正段为 (3+t)/4、(1-t)/4；共同仅 t=0 为1/2，其他为0；并集恒1。t=0 边缘各3/4。
- quadratic：A=t、B=U、C=V；[0,1]^2 均匀，k=1，目标 A，t∈[-1,2]。三个开段的边缘/共同/并集依次为0、t²、1。这里 alpha=2、d_A=1/2，验证浓度方向须通过原规则。
- spatial_tie：A=t、B=-t、C=0，纯矩形情景，目标 A/B，t∈[-1,1]。两侧各只有一站入选，t=0 两站共同入选概率1。它检验没有原子情景时仍须保留的孤立并列。

运行公开夹具：

    python3 -B path_examples.py baseline
    python3 -B path_examples.py verify
    python3 -B path_examples.py demo

baseline 仅用 V1 在有限手算点核对夹具，初始树应可通过；它不验证完整曲线。verify 在实现后先执行全部原测试，再核对公开曲线、冻结来源、整批拒绝、重放冲突、故障回滚、重开和离线交付。demo 展示实际曲线及更正后重开复算。夹具不代替完整性证明或独立核验，不以有限抽样宣称全部分界完备。

交付联动实现、简短交接不变量说明及独立业务核账：覆盖比较面平行/重合/同时相交、恒等评分、开段和分界值；重排队列时按站点运输方向，零方向、空目标及单点区间保持契约。为完整参数覆盖给出可检查的数学依据。复用 .pointwise-runtime.json 的现有结构，将 verify 接通原回归与新链路，将 demo 接通真实曲线及更正后重开；保留失败退出码，不能只打印成功。
