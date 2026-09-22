# documents/ —— 已归档的遗留目录

> **本目录不再使用。** 课堂助手的唯一数据真相源是 `classroom-data/`
> （8 个子目录，由 `src/application/data_dirs.py::DATA_LAYOUT_DIRS` 定义）。
>
> 旧布局到新布局的逐项映射见 [docs/legacy-input-output.md](../docs/legacy-input-output.md)。

现状：空目录（仓库根级的重复遗留；真实文档副本在 classroom-data/documents/）。

**新代码禁止向本目录写入。** 它保留在这里只是为了不让历史脚本/笔记里的路径
突然失效 —— 空的目录不会造成歧义，删掉反而会让引用它的旧文档指向不存在的路径。
