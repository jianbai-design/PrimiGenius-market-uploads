<!-- primigenius-market-submissions -->
# PrimiGenius 插件提交记录 / Plugin submissions

此处保存作者上传的插件及完整配置；审核和当前上架状态以平台市场记录为准。
Uploaded packages and full configurations are archived here. Review and listing status are maintained by the marketplace.

### SRA 高速下载（自动单双端） / SRA High-Speed Download \(Auto Layout\) · 2\.0\.1

- 插件 ID / Plugin ID: community\_242235968\_sra\_data\_download\_high\_speed\_auto
- 作者 / Owner: XvZiQiang
- 提交者 / Submitted by: jianbai-design
- 类型 / Type: cli
- 提交时间 / Submitted: 2026-11-16T12:22:31Z
- 安装包大小 / Size: 48796574 bytes
- SHA256: `8a62ea755538c142ab5ab64085e8b5bd5c4bd9f75e07df991b93442c8beb7208`

从 NCBI 官方 AWS 公共桶分片高速下载完整质量 SRA，逐个 accession 自动识别单端或双端，再用 fasterq-dump 与 pigz 生成并校验 FASTQ。插件自带离线运行环境，首次运行本地构建镜像，不依赖公共镜像站。 / Downloads full-quality SRA objects from the official NCBI AWS public bucket with resumable ranges, automatically detects SINGLE or PAIRED layout per accession, and creates verified FASTQ files with fasterq-dump and pigz\. The bundled offline runtime builds locally on first use without a public container registry\.

更新说明 / Changes: 优化界面参数排版

[源码 / Source](https://github.com/jianbai-design/PrimiGenius-market-uploads/tree/715d723605f769d2aa43cc4694427102a1722ee5/plugins/community_242235968_sra_data_download_high_speed_auto)

[完整配置与提交信息 / Full record](https://github.com/jianbai-design/PrimiGenius-market-uploads/blob/HEAD/submissions/submission-22d8ca9822754b269b347b4fc5d6caae.json) · [安装包 / Package](https://github.com/jianbai-design/PrimiGenius-market-uploads/releases/tag/submission-22d8ca9822754b269b347b4fc5d6caae) · [审核记录 / Review](https://github.com/jianbai-design/PrimiGenius-community-plugins/issues/18)

