# OCR服务性能优化报告

生成时间：2026-07-16

## 1. 当前系统架构

- Web框架：FastAPI 0.139.0
- 服务容器：Uvicorn 0.51.0
- Python版本：3.12.13
- OCR框架：RapidOCR 3.9.1
- 推理框架：ONNXRuntime 1.27.0
- 当前推理设备：CPU
- ONNXRuntime provider：`AzureExecutionProvider`、`CPUExecutionProvider`
- 可见逻辑CPU核心：8
- GPU状态：当前环境未检测到 `CUDAExecutionProvider`，也未检测到 `nvidia-smi`

请求链路：

1. 请求进入 `POST /api/measure/ocr`
2. FastAPI 读取 multipart 文件、`image_base64` 或 `image_path`
3. 进入 OCR 并发容量控制
4. 在线程池中执行 RapidOCR 同步推理
5. RapidOCR 执行检测、方向分类、识别
6. `measure_parser.py` 解析网络制式、位置、小区 ID、信号值
7. 返回 JSON

## 2. 当前性能问题

优化前压测数据来自 `reports/ocr_qps_results_20260716_103715.csv`。

历史峰值：

| CPU核心 | Worker | ORT线程 | 并发 | QPS | 平均RT | P95 | P99 |
|-:|-:|-:|-:|-:|-:|-:|-:|
| 8 | 1 | auto | 8 | 1.174 | 5659.1ms | 7380.8ms | 7429.4ms |

只读微基准显示：

| 项目 | 耗时 |
|-|-:|
| RapidOCR 初始化 | 0.518s |
| 单张 OCR 推理 | 1.4s-1.9s |
| 结果解析 | 0.639ms |
| base64 解码 | 0.12ms |

结论：接口低 QPS 的主要瓶颈是 CPU OCR 推理，不是 JSON、base64、FastAPI 或结果解析。

## 3. 性能瓶颈分析

1. CPU 推理耗时占绝对大头  
   当前没有 GPU provider，RapidOCR 的 det/cls/rec 均由 ONNXRuntime CPU 执行。

2. ONNXRuntime 默认线程在高并发下互相争抢 CPU  
   旧配置下单请求会尽可能使用 CPU 线程；并发请求叠加后，线程超卖导致吞吐提升有限、尾延迟上升。

3. 单纯增加 Uvicorn worker 收益不稳定  
   8 核环境下，`workers=2/4` 且 ORT auto 时没有明显提升，4 worker 甚至更差，说明根因不是单纯 ASGI worker 数不足。

4. 缺少背压  
   优化前高并发请求会继续进入等待，QPS提升有限，但 P95/P99 显著变差。

5. 模型懒加载影响冷启动首请求  
   初始化不是持续吞吐瓶颈，但会导致首个业务请求抖动。

## 4. 优化措施

### 修改内容

| 文件 | 修改目的 |
|-|-|
| `ocr_server.py` | 增加启动预加载、OCR 并发容量控制、RapidOCR/ONNXRuntime 环境变量参数化 |
| `tests/test_ocr_server.py` | 覆盖参数化、预加载、满载返回 429、原接口行为 |
| `benchmark_ocr_qps.py` | 支持 worker/ORT线程矩阵压测，修复报告中文输出，记录 worker 和 ORT 线程列 |
| `tests/test_benchmark_ocr_qps.py` | 覆盖压测命令、环境变量、中文报告表头、QPS统计 |
| `start_ocr_server.ps1` | 固化推荐启动参数，避免裸用默认配置 |

### 已实施优化

1. 模型启动预加载  
   通过 `OCR_PRELOAD=true` 在服务启动阶段加载 RapidOCR，避免首个业务请求承担初始化成本。

2. OCR 并发背压  
   通过 `OCR_MAX_CONCURRENCY` 控制同时进入 OCR 推理区的请求数。容量满时等待指定时间，超时返回 429，避免无限排队拖垮尾延迟。

3. ONNXRuntime 线程参数化  
   通过 `OCR_ORT_INTRA_THREADS` 和 `OCR_ORT_INTER_THREADS` 控制单请求推理线程，降低并发下 CPU 超卖。

4. worker/线程矩阵压测  
   增强压测脚本，实际测试 `workers=1/2/4`、`ORT线程=auto/1/2`。

## 5. 优化前后对比

同等压力对比：8核、并发16。

| 指标 | 优化前 | 优化后 |
|-|-:|-:|
| 配置 | 1 worker，ORT auto | 2 workers，ORT intra=1 |
| QPS | 1.111 | 1.325 |
| 平均RT | 12000.1ms | 11484.5ms |
| P95 | 14367.4ms | 12040.7ms |
| P99 | 未记录在旧矩阵摘要中 | 12056.9ms |
| 成功率 | 100% | 100% |
| CPU | CPUExecutionProvider，线程 auto | CPUExecutionProvider，单请求 ORT 线程限制为 1 |
| GPU利用率 | 无可用 GPU provider | 无可用 GPU provider |

收益：

- 同等并发16下，QPS 从 1.111 提升到 1.325，提升约 19.3%。
- 同等并发16下，P95 从 14367.4ms 降到 12040.7ms，下降约 16.2%。
- 相比历史峰值 1.174 QPS，优化后峰值 1.325 QPS，提升约 12.9%。

最新压测明细：

- `reports/ocr_qps_results_20260716_112434.csv`
- `reports/ocr_qps_results_20260716_112953.csv`
- 最佳稳定组合：8核、2 workers、ORT intra=1、并发16，QPS 1.325。
- 近似同等组合：8核、1 worker、ORT intra=1、并发16，QPS 1.321。

重要观察：

- `workers=2` 相比 `workers=1` 只有轻微提升。
- `ORT intra=1` 是本轮最有效的服务层参数。
- `workers=4` 或 `ORT intra=2` 在部分高并发组合下不稳定或收益下降，不推荐作为默认配置。

## 6. 推荐启动方式

推荐优先使用：

```powershell
.\start_ocr_server.ps1 -Port 8000 -Workers 2 -MaxConcurrency 16 -OrtThreads 1
```

如希望更保守、更少进程内存占用：

```powershell
.\start_ocr_server.ps1 -Port 8000 -Workers 1 -MaxConcurrency 16 -OrtThreads 1
```

## 7. 后续优化建议

1. 采集真实线上资源指标  
   建议补充 CPU 使用率、进程内存、上下文切换、请求排队时间、429 比例。

2. 评估关闭方向分类模型  
   如果输入截图方向稳定，可 A/B 测试 `OCR_USE_CLS=false`，可能降低单次推理耗时，但需验证旋转图片准确率。

3. 尝试 OpenVINO CPU 后端  
   当前是 CPU 推理场景，OpenVINO 可能比 ONNXRuntime CPU 更适合 Intel/通用 CPU 部署。

4. 启用 GPU 或 TensorRT  
   当前环境没有 CUDA provider。若生产机器有 NVIDIA GPU，需要安装并验证 `onnxruntime-gpu` 或 TensorRT，再重新压测。

5. 架构层引入 OCR worker 池  
   如果业务允许异步化，可将 API 与 OCR 推理解耦，用队列削峰，避免请求线程直接等待模型。

6. 多实例负载均衡  
   单机 CPU 推理上限明确后，继续提升吞吐应优先横向扩容，而不是在单进程内继续堆并发。
