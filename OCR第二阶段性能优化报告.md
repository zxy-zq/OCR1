# OCR第二阶段性能优化报告

生成时间：2026-07-16

## 1. 当前瓶颈

第一阶段已经把接口层和并发参数优化到较合理状态：

- 第一阶段最佳配置：8核 CPU、2 workers、ONNXRuntime intra threads=1
- 第一阶段最佳 QPS：1.325
- 第一阶段 P95：12040.7ms

第二阶段重新拆分 OCR 完整链路后，确认当前主要瓶颈不在 FastAPI、JSON、base64 或 worker 数，而在 RapidOCR 的 detection 模型 CPU 推理。

### 单张 OCR 阶段耗时拆分

配置：ONNXRuntime CPU、intra=1、inter=1、默认检测尺寸 `Det.limit_side_len=736`。

| 阶段 | 4G测试图 | 占比 | 5G测试图 | 占比 |
|-|-:|-:|-:|-:|
| 图片读取/解码/颜色转换 | 5.1ms | 0.3% | 5.8ms | 0.3% |
| 全局 resize/preprocess | 0.01ms | ~0% | 0.02ms | ~0% |
| detection preprocess | 36.4ms | 1.9% | 38.5ms | 1.8% |
| detection ONNX 推理 | 1657.5ms | 84.2% | 1816.0ms | 84.6% |
| detection postprocess | 5.7ms | 0.3% | 7.1ms | 0.3% |
| crop 文本区域 | 0.8ms | ~0% | 0.7ms | ~0% |
| classification | 4.8ms | 0.2% | 5.5ms | 0.3% |
| recognition | 257.8ms | 13.1% | 272.5ms | 12.7% |
| final output | 0.3ms | ~0% | 0.3ms | ~0% |
| 总计 | 1968.4ms | 100% | 2146.4ms | 100% |

结论：detection ONNX 推理占 84% 以上，是当前吞吐低的根因。

## 2. 优化方案

### 已实施

| 优化项 | 修改内容 | 目的 |
|-|-|-|
| OCR 阶段埋点 | `run_rapidocr` 返回带 profile 的 OCR 文本列表，请求日志输出 `det_ms/cls_ms/rec_ms/other_ms/total_ms` | 让线上请求能看到 OCR 阶段耗时 |
| detection 尺寸参数化 | 新增环境变量 `OCR_DET_LIMIT_SIDE_LEN` | 通过降低 detection 输入尺寸减少 CPU 推理计算量 |
| 启动脚本参数化 | `start_ocr_server.ps1` 新增 `-DetLimitSideLen` | 生产启动时可直接指定检测尺寸 |
| 压测矩阵增强 | `benchmark_ocr_qps.py` 新增 `--det-limit-side-len` | 可直接跑 736/640/512/416 对比 |
| 测试覆盖 | 更新服务和压测脚本测试 | 保证参数、日志、报告输出不回退 |

### 未作为主优化

| 方案 | 结论 |
|-|-|
| 增加 worker | 第一阶段已验证收益有限，且可能造成 CPU 争抢 |
| 关闭方向分类 `OCR_USE_CLS=false` | 小样本准确率无变化，但 cls 仅约 5ms，不是瓶颈；API 压测未带来稳定收益 |
| Batch 推理 | 当前瓶颈是 detection，RapidOCR 现有 batch 主要在 cls/rec 阶段；短期不优先 |
| OpenVINO | 当前环境未安装，需要后续单独验证 |
| GPU/TensorRT | 当前环境没有 CUDA provider，需要生产硬件和依赖支持后再评估 |

## 3. 修改文件

| 文件 | 说明 |
|-|-|
| `ocr_server.py` | 增加 `OCR_DET_LIMIT_SIDE_LEN`、OCR profile、请求阶段日志 |
| `start_ocr_server.ps1` | 增加 `-DetLimitSideLen` 参数 |
| `benchmark_ocr_qps.py` | 增加检测尺寸压测矩阵、CSV/Markdown 检测尺寸列 |
| `tests/test_ocr_server.py` | 覆盖 detection 参数和 profile 日志 |
| `tests/test_benchmark_ocr_qps.py` | 覆盖检测尺寸压测环境、汇总结果和报告表头 |

## 4. 压测数据

### detection 尺寸 API 压测

测试图片：`measure_5g_test.png`  
配置：8核、2 workers、ORT intra=1、并发16、单轮5秒。

| Det.limit_side_len | QPS | 平均RT | P95 | 成功率 |
|-:|-:|-:|-:|-:|
| 736 | 1.819 | 8244.6ms | 8731.9ms | 100% |
| 640 | 2.419 | 6244.6ms | 6576.7ms | 100% |
| 512 | 3.244 | 4196.4ms | 5504.1ms | 100% |
| 416 | 3.417 | 4027.5ms | 5261.6ms | 100% |

另一次 416 压测结果为 3.610 QPS、P95 5116.1ms，说明 416 在本机样本上具备较高吞吐潜力。

注意：512 曾出现一次异常运行，22/27 成功、5 个错误；复测后 23/23 成功。因此生产推荐需要灰度和监控错误率。

### 准确率小样本回归

样本：`measure_4g_test.png`、`measure_5g_test.png`、4 张真实截图，共 6 张。  
对比基线：`Det.limit_side_len=736`。

| Det.limit_side_len | 平均单张耗时 | 与736解析字段一致数 | 差异数 |
|-:|-:|-:|-:|
| 736 | 4462.3ms | 6/6 | 0 |
| 512 | 3888.6ms | 6/6 | 0 |
| 416 | 3845.5ms | 6/6 | 0 |

结论：当前小样本中 512 和 416 未发现解析字段变化，但样本数仍偏少，生产前建议继续扩充截图集回归。

## 5. QPS 提升比例

以第一阶段最佳 `1.325 QPS` 为基线：

| 配置 | QPS | 相对第一阶段提升 |
|-|-:|-:|
| 第一阶段最佳 | 1.325 | - |
| Det.limit_side_len=512 | 3.244 | +144.8% |
| Det.limit_side_len=416 | 3.417 | +157.9% |

以同一张 5G 图、同一压测条件下的 736 为基线：

| 配置 | QPS | 相对736提升 |
|-|-:|-:|
| 736 | 1.819 | - |
| 640 | 2.419 | +33.0% |
| 512 | 3.244 | +78.3% |
| 416 | 3.417 | +87.9% |

## 6. P95 变化

以第一阶段 P95 `12040.7ms` 为基线：

| 配置 | P95 | 变化 |
|-|-:|-:|
| 第一阶段最佳 | 12040.7ms | - |
| Det.limit_side_len=512 | 5504.1ms | -54.3% |
| Det.limit_side_len=416 | 5261.6ms | -56.3% |

以同一张 5G 图、同一压测条件下的 736 为基线：

| 配置 | P95 | 相对736变化 |
|-|-:|-:|
| 736 | 8731.9ms | - |
| 640 | 6576.7ms | -24.7% |
| 512 | 5504.1ms | -37.0% |
| 416 | 5261.6ms | -39.7% |

## 7. 推荐生产配置

优先推荐灰度配置：

```powershell
.\start_ocr_server.ps1 -Port 8001 -Workers 2 -MaxConcurrency 16 -OrtThreads 1 -DetLimitSideLen 416
```

如果灰度中出现漏检、字段缺失或错误率升高，切换到更保守配置：

```powershell
.\start_ocr_server.ps1 -Port 8001 -Workers 2 -MaxConcurrency 16 -OrtThreads 1 -DetLimitSideLen 512
```

保守线上策略：

1. 先用 416 灰度 10%-20% 流量。
2. 监控 2xx 成功率、429 数量、OCR 解析字段空值率、P95、P99。
3. 如果字段空值率没有升高，再扩大流量。
4. 如果准确率波动，回退到 512。

## 8. 后续优化方向

1. 扩充准确率回归集  
   当前只做了 6 张样本回归。建议覆盖不同机型、不同截图尺寸、暗色/亮色主题、模糊图、长截图。

2. 增加独立准确率回归脚本  
   将 736 作为基线，对比 640/512/416 的 `network/location/cell_id/signal` 差异，输出 CSV。

3. 评估 OpenVINO  
   当前环境未安装 OpenVINO。由于瓶颈是 CPU detection 推理，OpenVINO 可能继续提升吞吐。

4. 模型级优化  
   后续可评估 detection 模型量化或更轻量模型，但需要准确率验证。

5. 架构层 OCR worker 池  
   如果业务流量继续增长，建议将 FastAPI 与 OCR 推理解耦，用有界队列和 worker 池控制排队、削峰和横向扩容。

## 9. 结论

第二阶段确认：当前瓶颈是 RapidOCR detection ONNX CPU 推理。通过参数化并降低 `Det.limit_side_len`，QPS 从第一阶段的 1.325 提升到 3.417，P95 从 12040.7ms 降到 5261.6ms。当前推荐在生产中灰度 `DetLimitSideLen=416`，保守回退值为 512。
