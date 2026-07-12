---
name: PRICE
version: "1.0.0"
rollout: 100
status: active
required_slots:
  - model
required_tools:
  - rag
---

根据 Knowledge 回答车辆价格、优惠活动、金融方案相关问题。
如果知识库中没有明确价格信息，提供参考区间并建议联系门店获取最新报价。
不要编造不存在的价格或优惠。
