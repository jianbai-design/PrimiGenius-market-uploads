# 测试报告

测试日期：2026-11-13（Asia/Shanghai）  
插件版本：2.0.0  
插件 ID：`sra_data_download_high_speed_auto`

## 离线镜像构建

- 使用 `podman build --network=none --pull=never` 从插件内 `runtime/rootfs.tar.gz` 构建成功。
- Dockerfile 基础为 `FROM scratch`，构建过程未访问任何公共镜像站。
- 生成镜像：`localhost/primigenius/sra-fastq-auto:2.0.0`。
- rootfs SHA-256：`AFE7C6AB3860F4F93FBFE0C2877B0016F6E440FE48E0D1550CD88AA5F1B02248`。
- 运行环境验证：SRA Toolkit 3.4.1、pigz 2.8、Python 3.9.25。

## 单元测试

容器内运行 25 项测试，全部通过。覆盖：

- accession 输入解析和去重；
- 固定分片、断点续传、过期检查点清理；
- SRA 组装和校验；
- SINGLE/PAIRED 自动识别；
- 非标准 read 编号标准化；
- gzip 完整性和 MD5；
- 完成清单的单双端布局复核；
- 扁平输出隔离、汇总状态和终态写入。

## 真实端到端测试

同一次批处理使用一个真实单端 run 和一个真实双端 run：

| Accession | NCBI/ENA 布局 | fasterq-dump 原始输出 | 最终输出 | 结果 |
| --- | --- | --- | --- | --- |
| DRR002792 | SINGLE | `DRR002792_4.fastq` | `DRR002792.fastq.gz` | 自动识别、标准化、压缩、gzip/MD5 校验成功 |
| DRR051807 | PAIRED | `_1.fastq`、`_2.fastq` | `_1.fastq.gz`、`_2.fastq.gz` | 自动识别、压缩、gzip/MD5 校验成功 |

结果文件直接位于 `s3_full_quality_fastq` 根目录，SRA 直接位于 `.s3_sra_cache` 根目录。第二次重跑时 SINGLE 和 PAIRED 均完成清单复核并显示 `SKIPPED`，最终退出码为 0，只有一个 `WORKFLOW_COMPLETE status=SUCCESS`。

## 结论

插件可以在同一 accession 列表中混合处理单端和双端数据；目标电脑无需公共容器镜像站即可从自由市场安装包本地构建运行镜像。
