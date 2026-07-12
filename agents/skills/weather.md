---
name: WEATHER
version: "1.0.0"
rollout: 100
status: active
required_slots:
  - location
required_tools:
  - weather
---

根据【天气信息】中的内容回答用户当地的天气情况。
包括当前温度、天气状况、湿度、风向风速等。
如果有未来预报，可以顺带提及。
