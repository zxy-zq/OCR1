# OCR第三阶段性能优化报告

生成时间：2026-07-16

## 1. 当前性能状态

第二阶段最优配置为：

- CPU：8核
- Web worker：2
- ONNX Runtime intra threads：1
- RapidOCR `Det.limit_side_len`：416
- Detection模型：`PP-OCRv6_det_small.onnx`
- 基线QPS：约 3.417
- 基线P95：约 5261ms

第三阶段继续验证后确认，当前主要瓶颈不是 FastAPI，也不是 worker 数量，而是 RapidOCR detection ONNX CPU 推理。

## 2. 优化目标

本阶段目标是优先降低 detection 推理耗时，进一步提升 OCR 整体吞吐量，并尽量控制关键字段识别准确率风险。

## 3. 当前瓶颈

在 `measure_5g_test.png`、`Det.limit_side_len=416`、`Global.use_cls=false`、ORT intra/inter=1 下，RapidOCR detection 阶段拆分如下：

| Detection模型 | det总耗时 | 预处理 | ONNX推理 | 后处理 | 完整OCR平均耗时 |
|-|-:|-:|-:|-:|-:|
| small | 937.1ms | 21.4ms | 912.6ms | 3.0ms | 1190.2ms |
| tiny | 125.9ms | 20.2ms | 103.0ms | 2.6ms | 431.2ms |

结论：

- detection 阶段中，ONNX 推理占绝大部分耗时。
- tiny detection 模型可以显著降低 detection 推理成本。
- 预处理和 DB 后处理不是当前主要瓶颈。

## 4. 实施方案

本阶段实施 P0：支持可配置的 RapidOCR detection 模型类型。

新增配置：

| 配置 | 说明 |
|-|-|
| `OCR_DET_MODEL_TYPE=tiny` | 使用 `PP-OCRv6_det_tiny.onnx` |
| `OCR_DET_MODEL_TYPE=small` | 使用 `PP-OCRv6_det_small.onnx` |
| `OCR_DET_MODEL_TYPE=medium` | 使用 medium detection 模型 |
| 不设置 | 保持 RapidOCR 默认行为 |

推荐启动命令：

```powershell
.\start_ocr_server.ps1 -Port 8001 -Workers 2 -MaxConcurrency 16 -OrtThreads 1 -DetLimitSideLen 416 -DetModelType tiny
```

## 5. 修改文件

| 文件 | 修改内容 |
|-|-|
| `ocr_server.py` | 增加 `OCR_DET_MODEL_TYPE` 环境变量解析，支持 tiny/small/medium |
| `start_ocr_server.ps1` | 增加 `-DetModelType` 启动参数 |
| `benchmark_ocr_qps.py` | 增加 `--det-model-type` 压测参数，并写入 CSV/Markdown 报告 |
| `tests/test_ocr_server.py` | 增加服务配置测试 |
| `tests/test_benchmark_ocr_qps.py` | 增加压测参数透传测试 |

## 6. 压测数据

压测配置：

- 图片：`measure_5g_test.png`
- CPU：8核
- workers：2
- ORT intra threads：1
- `Det.limit_side_len`：416
- 并发：16
- 单轮时长：10秒

| Detection模型 | QPS | 平均RT | P95 | 成功率 |
|-|-:|-:|-:|-:|
| small | 3.686 | 4081.8ms | 5488.5ms | 100% |
| tiny | 6.586 | 2346.9ms | 3142.0ms | 100% |

提升：

- QPS 提升：约 78.7%
- 平均RT 下降：约 42.5%
- P95 下降：约 42.8%

本轮明细：

- CSV：`reports/ocr_qps_results_20260716_132607.csv`
- 压测报告：`reports/ocr_qps_report_20260716_132607.md`

## 7. 准确率对比

使用现有 4G/5G 图片抽样 10 张，对比 small 与 tiny：

| 指标 | 结果 |
|-|-:|
| 样本数 | 10 |
| 原始OCR文本完全一致 | 2/10 |
| 业务关键字段解析一致 | 10/10 |

结论：

- tiny 模型会改变部分原始 OCR 文本结果。
- 当前样本中，网络类型、经纬度、小区ID、RSRP 等关键业务字段解析结果一致。
- 生产切换前建议继续用更多真实线上样本验证，重点关注小字、模糊、低对比、截图不完整场景。

## 8. 优化收益

| 指标 | 优化前 small | 优化后 tiny |
|-|-:|-:|
| QPS | 3.686 | 6.586 |
| 平均RT | 4081.8ms | 2346.9ms |
| P95 | 5488.5ms | 3142.0ms |
| 成功率 | 100% | 100% |

本阶段优化收益超过 10%，值得继续保留并进入更大样本验证。

## 9. 推荐生产方案

建议先灰度使用以下配置：

```powershell
.\start_ocr_server.ps1 -Port 8001 -Workers 2 -MaxConcurrency 16 -OrtThreads 1 -DetLimitSideLen 416 -DetModelType tiny
```

灰度观察指标：

- QPS
- 平均RT
- P95/P99
- 小区ID漏识别率
- RSRP漏识别率
- 坐标解析失败率

如果关键字段一致率低于 99%，建议回退到：

```powershell
.\start_ocr_server.ps1 -Port 8001 -Workers 2 -MaxConcurrency 16 -OrtThreads 1 -DetLimitSideLen 416 -DetModelType small
```

## 10. 后续优化方向

1. 在 tiny 模型下重新测试 ORT intra/inter 组合，重点比较吞吐而不是单请求耗时。
2. 扩大准确率样本集，至少覆盖 4G/5G、不同分辨率、模糊、压缩、暗色截图。
3. 评估 OpenVINO Execution Provider，当前环境尚未安装 OpenVINO。
4. 评估 INT8 量化，当前环境缺少 `onnx` 解析包，需补齐工具链后验证精度。
5. GPU/TensorRT 属于部署环境升级方向，适合在 CPU 优化收益见顶后推进。
