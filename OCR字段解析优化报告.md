# OCR字段解析优化报告

生成时间：2026-07-16

## 1. 当前问题

第三阶段切换 tiny detection 后，OCR 原始文本中可以看到 `RSRP` 和对应数值，但业务字段 `signal` 经常解析为空。

典型 OCR 形式：

```text
RSRP
-76
```

```text
RSRP
:
-111
```

```text
RSRP(dBm)
- 105
```

还有 Cellular-Z 指标表形式：

```text
RSSI
RSRP
RSRQ
SINR
-77
-76
-5
1.2
```

旧规则只支持类似 `RSRP -76` 这种较固定的同行格式，无法覆盖 OCR 换行和指标表结构。

## 2. 原因分析

当前解析逻辑问题：

| 字段 | 当前情况 | 问题 |
|-|-|-|
| network | 通过 `NR-CI` 或 `SS-RSRP` 判断 | 基本可用 |
| location | 通过经纬度正则提取 | 对 `0/0` 不会提取，符合预期 |
| cell_id | 通过 `ECI` / `NR-CI` 提取 | 基本可用 |
| signal | 旧规则要求 `RSRP` 后紧跟数值 | 无法处理换行、冒号拆分、指标表 |
| PCI / EARFCN / SINR | 当前未作为业务字段返回 | 暂未实现解析 |

根因：

1. 旧 signal 正则依赖固定文本结构。
2. OCR 会把标签和值拆成多行。
3. 部分截图是表格结构，`RSRP` 的值需要按指标列位置匹配。
4. 不能简单搜索所有负数，否则会把 RSRQ、邻区 RSRP、图表刻度误当成服务小区 RSRP。

## 3. 修改文件

| 文件 | 修改内容 |
|-|-|
| `measure_parser.py` | 新增 signal 上下文解析、debug 解析能力 |
| `tests/test_measure_parser.py` | 增加 RSRP 多行、冒号、单位、表格、防误匹配测试 |

## 4. 新解析策略

采用：

```text
关键词定位
↓
局部上下文窗口
↓
合法 dBm 值校验
↓
无法确认则返回 ""
```

具体规则：

1. 优先匹配明确形式：`RSRP -76`、`RSRP: -76`、`RSRP(dBm) -76`、`SS-RSRP -110`。
2. 支持 OCR 换行后重新拼接：`RSRP`、`:`、`-105 dBm`。
3. 支持 LTE 指标表：`RSSI RSRP RSRQ SINR` 后续值按列位置匹配。
4. 支持 5G `SS / RSRP / RSRQ / SINR` 拆分表格，优先取服务小区 SS-RSRP。
5. 仅接受合法 dBm 范围：`-140 ~ -40`。
6. 找不到可信 RSRP 上下文时返回空字符串 `""`。

## 5. Debug能力

新增：

```python
debug_parse_measure_ocr(lines)
```

返回内容：

```python
{
    "raw_lines": [...],
    "parsed": {...},
    "empty_reasons": {...}
}
```

用于保存 OCR 原始文本、解析结果和空字段原因。

## 6. 测试案例

新增测试覆盖：

| 场景 | 预期 |
|-|-|
| `RSRP -76` | `-76` |
| `RSRP` / `-111` | `-111` |
| `RSRP:` / `-105 dBm` | `-105` |
| `RSRP(dBm)` / `- 105` | `-105` |
| 没有 RSRP | `""` |
| 多个负数但只有一个 RSRP 上下文 | 只取 RSRP 对应值 |
| LTE 指标表 | 按 `RSRP` 列取值 |
| 5G SS 指标表 | 按 `SS-RSRP` 取值 |

测试结果：

```text
Ran 10 tests in 0.002s
OK
```

## 7. 修改前后数据

样本：现有 4G/5G 截图 68 张，tiny detection，`Det.limit_side_len=416`。

| 字段 | 修改前空值 | 修改后空值 |
|-|-:|-:|
| network | 0 | 0 |
| location | 5 | 5 |
| cell_id | 0 | 0 |
| signal | 62 | 0 |

signal 填充情况：

| 指标 | 数量 |
|-|-:|
| 总样本 | 68 |
| signal 成功解析 | 68 |
| signal 返回空字符串 | 0 |

补充修正：

```text
测试截图\4G\微信图片_20260510183742.jpg
```

该图原始 OCR 文本包含：

```text
RSSI
RSRP
RSRQ
SINR
RXLEV
-73
-137
-4
24.0
-65
```

其中 `-137` 是 `RSRP` 对应值。最初规则未识别 `RXLEV` 表格列，导致列解析提前中断；已补充 `RXLEV` 为指标标签后，该图 `signal=-137`。

## 8. 可能风险

1. 当前没有人工标注真值，修改前后数据是基于 OCR 文本和业务解析结果的回归验证。
2. 指标表解析依赖 `信号强度` 附近的标签和值顺序，如果 OCR 顺序严重错乱，仍会返回空字符串。
3. 解析器不会为了覆盖率搜索全局负数，因此极端截图可能仍为空，但能降低误解析风险。
4. PCI、EARFCN、SINR 当前未纳入返回字段，如后续需要，应单独设计字段级上下文规则。

## 9. 结论

本阶段优化提升了 `signal` 字段完整率，同时保持“准确优先”的策略：

- 不全局扫描负数。
- 不猜测未知值。
- 没有可信 RSRP 上下文时返回 `""`。

建议下一步对 `location=0/0`、PCI、EARFCN、SINR 另开字段规则设计，不与 signal 规则混在一起。
