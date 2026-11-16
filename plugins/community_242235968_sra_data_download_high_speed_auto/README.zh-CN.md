# SRA 高速下载（自动单双端）

版本：2.0.0

这是可提交到 PrimiGenius 自由市场的共享插件。它从 NCBI 官方 AWS Open Data 公共桶下载完整质量 SRA，在本地运行 `fasterq-dump`，自动识别每个 accession 的 SINGLE/PAIRED 布局，再用 `pigz` 压缩并校验 FASTQ。

## 自带离线镜像

插件的 `runtime/rootfs.tar.gz` 包含完整 Linux 运行环境。`Dockerfile` 使用 `FROM scratch`，所以目标电脑首次运行时可直接在本地构建 `localhost/primigenius/sra-fastq-auto:2.0.0`，不访问 Docker Hub、Quay 或第三方镜像站，也不需要代理。

首次运行需要等待本地解包和镜像构建；后续运行直接复用镜像。

## 自动识别规则

- 仅生成一个合法生物学 read FASTQ：判定为 `SINGLE`，统一命名并压缩为 `ACC.fastq.gz`。兼容老 SRA 出现的 `ACC_4.fastq` 等非标准 read 编号。
- 生成两个带不同数字 read 后缀的 FASTQ：判定为 `PAIRED`，按原始 read 编号排序后统一命名为 `_1.fastq.gz` 与 `_2.fastq.gz`。
- 缺少一端、出现额外 FASTQ 或其他异常组合：拒绝发布，防止把残缺双端数据误当单端。

每个 accession 独立识别，因此同一批列表可以同时包含单端和双端 run。

## 输出布局

```text
输出目录/
├─ s3_full_quality_fastq/
│  ├─ SRR_SINGLE.fastq.gz
│  ├─ SRR_PAIRED_1.fastq.gz
│  └─ SRR_PAIRED_2.fastq.gz
├─ .s3_sra_cache/
│  ├─ SRR_SINGLE.sra
│  └─ SRR_PAIRED.sra
├─ .s3_fastq_manifests/
│  ├─ SRR_SINGLE.json
│  └─ SRR_PAIRED.json
├─ s3_fastq_download_summary.tsv
└─ .s3_fastq_status.json
```

FASTQ 和 SRA 均为扁平目录，不为每个 accession 新建结果子目录。汇总表会记录 `layout`、状态和最终文件。

## 完整性与恢复

- 固定 64 MiB 分片并行下载，可从已写入字节断点续传。
- SRA 先检查对象大小，再执行 `vdb-validate`。
- FASTQ 经 pigz 压缩后逐文件执行完整 gzip 解码和 MD5。
- 完成清单同时记录 SINGLE/PAIRED 布局、文件大小和 MD5；重跑时校验后跳过。
- 转换、压缩失败或用户中止时，清理未压缩工作目录，保留已校验 SRA 和可续传下载分片。
- 全部 accession 处理结束后写入唯一 `WORKFLOW_COMPLETE` 终态，避免持续输出空状态。

## 推荐参数

| 参数 | 默认值 | 建议 |
| --- | ---: | --- |
| S3 分段连接数 | 8 | 一般使用 8；拥塞时降到 4 或 2 |
| 转换/压缩线程 | 12 | 常用 8–16，不要超过可用 CPU 线程 |
| 自动重试次数 | 8 | 每个分片独立重试并续传 |
| 保留已校验 SRA | 开启 | 便于重新生成 FASTQ，空间紧张时可关闭 |

## 自由市场发布

在 PrimiGenius 的“自由市场 → 上传插件”中选择本插件目录或生成的 ZIP，版本填写 `2.0.0`。安装包必须完整保留 `runtime/rootfs.tar.gz`；它是离线构建镜像所需的运行环境。

自由市场会在审核发布时将源插件 ID 转换为 `community_<GitHub数字ID>_sra_data_download_high_speed_auto`，这是正常行为。
