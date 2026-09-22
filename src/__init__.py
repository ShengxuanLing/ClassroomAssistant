# Classroom Assistant
#
# 产品版本的**唯一真源**是 ``src.application.workspace.APPLICATION_VERSION``
# （``/api/health`` 报的就是它）。这里的 ``__version__`` 只是包元数据, 必须与它一致。
#
# 历史: 这里曾写死 "2.0.0", 而产品实际版本是 0.35.0 —— 仓库里存在两个互相矛盾的
# 版本号, 且两处都没有被任何代码使用, 所以一直没人发现。现在由
# ``tests/test_bootstrap.py::TestVersionMetadata`` 钉住一致性, 再漂移就会红。
__version__ = "1.0.1"
