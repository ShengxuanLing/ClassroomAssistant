# database/ —— 已归档的遗留目录

> **本目录不再使用。** 课堂助手的唯一数据真相源是 `classroom-data/`
> （8 个子目录，由 `src/application/data_dirs.py::DATA_LAYOUT_DIRS` 定义）。
>
> 旧布局到新布局的逐项映射见 [docs/legacy-input-output.md](../docs/legacy-input-output.md)。

现状：**非空**：含一个历史 classroom.sqlite（2026-09-18 遗留）。活跃数据库是 ``classroom-data/database/classroom.sqlite``。本文件未被任何代码读取，保留仅作存档，**不要**当作真相源。

**新代码禁止向本目录写入。** 它保留在这里只是为了不让历史脚本/笔记里的路径
突然失效 —— 空的目录不会造成歧义，删掉反而会让引用它的旧文档指向不存在的路径。
