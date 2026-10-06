# 制造零部件供应协同服务

本项目是使用 Python、FastAPI 与 SQLite 实现的服务端应用，覆盖车型、物料、BOM、供应商、采购、到货、检验、库存和短缺分析。它可在单个 Linux 应用容器内完成安装、测试、编译和接口验收，不依赖浏览器、外部数据库、缓存、消息队列或额外运行服务。

## 安装

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
```

## 测试

```bash
python3 -m pytest -q tests
```

## 编译

```bash
python3 -m compileall -q .
```

## 接口验收

```bash
python3 -c "from app.main import app; assert len(app.routes) > 5; print(len(app.routes))"
```

## 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 替代料分配方案

多个生产批次同时缺主料时，`POST /api/v1/allocations/solve` 会把主料及替代料的
**有限库存批次**（现有库存、在途采购单、已确认供应商承诺分批）真正分配到具体生产批次，
分配依据包括：车型允许范围（替代限制）、替代换算比例与最大替代比例
（`PUT /api/v1/alternatives/{alt_id}/ratio`）、批次/车型优先级与交期。

- 求解结果为 `draft` 草稿，**生成即预留库存**；其他草稿/已冻结方案的占用都会计入，
  同一物料的求解经物料级锁串行化，两个计划员并发求解不会重复承诺同一库存。
- `POST /api/v1/allocations/{id}/confirm` 由计划员确认后冻结，支持 `accepted_batch_ids`
  部分确认（未接受批次的预留立即释放）；冻结前会二次校验库存未被挪用。
- `POST /api/v1/allocations/{id}/cancel` 取消方案并释放全部预留。
- 冻结后库存或供应承诺发生变化时，`POST /api/v1/allocations/{id}/recheck`
  只生成差异建议（减少、隔离、交期变更、新增供应），**绝不改写已确认结果**。
- 求解时同时按"优先级交期综合/交期优先/优先级绝对优先"三种策略计算，
  返回落选批次原因及改用其他策略会影响哪些批次的交期。
