# 有限浓度修正路径：交接说明与独立核账

本文说明 V1 基线上新增的两级成果：固定评分路径 `score_path` 与完整概率曲线
`path_report`。业务契约见 `PATHS.md`，手算夹具见 `path_examples.py`。

## 1. 职责划分

- 评分职责（`monitor/scoring/paths.py`）：拥有站点坐标、浓度方向与参数化系数。
  路径载荷固定 `score_id`、站点顺序、逐站方向、四项系数 `[a,b,c,e]`、`lo`/`hi`，
  并在读取/复算时与父观测、固定规则和方向重新核账。
- 排名分析职责（`monitor/analysis/curves.py`）：只消费已保存路径及来源校准模型，
  不接受另传方向或当前修订。它产出有序分界、开段二次多项式与实际分界值。
- 交付职责（`monitor/archive/delivery.py`）：`build_bundle` / `export_result` /
  `verify_bundle` 新增 `path_report` 根，沿用版本 1 编码、闭包、摘要与原子替换。

`x_i(t) = x_i + t*d_i` 只改浓度通道，`e = alpha * d_i`，父观测不被修改，也不产生
观测更正或单点任务。

## 2. 父链与冻结不变量

- `score_path -> score -> cohort/model -> observation/calibration_revision`
- `path_report -> score_path`
- 父类型在 `_parent_types` 中类型化；`resolve_lineage` 与发布钩子共同校验。
- 路径固化 `{"score": score_id}`，曲线固化 `{"path": path_id}`。
- 观测/校准更正、方向对象被外部修改、数据库关闭重开都不改变已发布成果。
- `read_result` / `export_result` 只读取固定数据；重开读取不触发
  `compile_scores` / `iter_event_regions` / `curve_for_path`（测试用
  `sys.setprofile` 明确禁止这些调用）。
- 请求命名空间 `score_path`、`analyze_path` 彼此独立，也与 V1 的 `score`/
  `analyze` 独立；同身份异输入抛 `RequestConflictError`。

## 3. 曲线完备性依据（可检查的数学论证）

参数 `t` 只进入各站评分的常数项，因此任意两站的比较线保持固定斜率、随 `t`
平移；比较线集合是平面上的直线排列，评分选择在排列的每个胞腔内恒定。

对固定矩形 `[u_lo,u_hi]×[v_lo,v_hi]`，排列的组合结构只在下列参数处变化：

1. 某条比较线与矩形顶点相交（胞腔的"进出点"改变）；
2. 两条非平行比较线的交点在矩形边上（三线以上的顶点出现/消失）；
3. 两条平行比较线重合（顺序同时互换）；
4. 三条比较线共点（有界胞腔可以出现或消失）；
5. 空间恒等比较（`b=c=0`）改变符号；
6. 点情景处某两站评分相等。

`candidate_times` 枚举这六类参数并裁剪到 `(lo,hi)`。在第 1–5 类相邻参数之间，
每个活动胞腔的顶点都是"两条固定斜率直线"的交点，随 `t` 仿射移动，因此面积是
`t` 的二次多项式；点情景的指示量在相邻参数之间恒定。所以每个开段内边缘、共同
与并集均为次数 ≤ 2 的多项式。实现用三点精确插值确定该多项式，再用另一组内部点
与 `integrate_ranking_event` 的精确单点积分比对（不一致即报错），不以采样替代
完整曲线。

分界合并：仅当左右系数向量逐项相同**且**该分界处的实际概率等于多项式的值时删除，
反复执行至不可删；两个端点保留。`t=0` 处并列质量的孤立值、点情景跳变、空间恒等
评分导致的孤立并列都因"实际值与多项式不符"而被保留（见 `mixed`、`spatial_tie`）。

`lo=hi` 时返回 `breakpoints=[lo]`、`pieces=[]`、单项 `values`。

## 4. 独立核账覆盖

`tests/test_path_curves.py` 覆盖：

- 三个手算夹具的逐项载荷（开段多项式与分界值、孤立值、请求与父引用）；
- 比较面共点（`test_concurrent_comparison_planes`）、平行面重合
  （`test_parallel_planes_become_coincident`）、空间恒等比较
  （`test_constant_comparison_and_two_targets`）；
- 空目标、单点区间、零方向、零权情景与重叠矩形；
- 队列换序时按站点运输方向（`test_reordered_cohort_transports_directions`）；
- 非法方向/区间/父类型整次拒绝且不发布；非法分析请求拒绝；
- `payload`/`parents`/`binding` 故障回滚与同请求稳定重试；
- 更正、对象修改、重开后旧成果不变；
- `path_report` 导出、`write`/`before_replace` 故障保全、删除原库后离线复算、
  篡改分界值或伪造方向系数均被拒绝；
- V1 报告包继续可导出、可复算。

`path_examples.py verify` 先跑全部原测试（V1 + 新增），再对公开夹具执行完整链路；
`demo` 额外展示真实曲线、更正后重开与两条曲线的离线复算。

## 5. 运行清单

`.pointwise-runtime.json`：

- `verify`: `python3 -B path_examples.py verify` —— 失败返回非零。
- `demo`: `python3 -B path_examples.py demo` —— V1 清单演示 + 三例曲线 + 更正重开。

原入口仍可直接运行：

    python3 -B -m unittest discover -s tests
    python3 -B -m monitor.analysis.jobs demo --manifest run_manifest.json
