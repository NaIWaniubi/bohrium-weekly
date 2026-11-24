# 回滚说明

本次整理只生成 `organized/` 下的常规文件副本，未移动、改名、覆盖或删除任何源文件。
回滚时删除 `organized/` 即可；逐文件对应关系见 `organized/manifest.json`。
若异常中断后存在 `.organized.backup/`，将它改回 `organized/` 即可恢复上一版；源文件始终未修改。
精确重复项在 `organized/aliases.json` 中记录为 canonical 别名，不产生第二份副本。
