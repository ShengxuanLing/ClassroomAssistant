# -*- coding: utf-8 -*-
"""中立基础件 (Task 35 / Task 44 / P1-4)。

本包只放**零层依赖**的运行时原语: 任何其它包 (domain / persistence /
application / backup / api / web) 都可以安全地依赖它, 而它不依赖任何项目代码。

``Clock`` / ``utc_now_iso`` / ``fixed_clock`` 的真源就在这里 —— 因为它们是
"全项目唯一的确定性来源", 既被持久化层 (迁移台账时间戳) 需要, 也被应用层
(日志 / 业务元数据) 需要。放在中立的 ``src.common`` 里, 两个方向都不会形成
逆向环 (P1-4)。历史调用方 ``from src.application.runtime import Clock`` 现在由
该模块 re-export。
"""
