# Groden Truth Set 多意图复杂评测数据集（合规修正版）

说明：共9类主意图，每类15条样本，均为复杂长难句、多子任务、多槽位，部分包含多轮上下文对话，严格匹配意图\-子任务映射规则。
合规规范：1\. 子任务严格遵循指定映射字典，无额外自定义subtask；2\. 槽位仅保留白名单：model、budget、phone、wechat、issue、product、name、lead\_refused，删除所有无效槽位

## 一、primary\_intent: greeting（15条）

\{"query":"'user':'你好呀，我想了解一下新款M9的落地价和配置，还想问问能不能免息分期'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'哈喽，打扰一下，我预算25万，想看看新款车型的参数，同时咨询下购车全款优惠'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"budget": "25万"\}\}

\{"query":"'user':'您好，之前咨询过M8的配置，现在想问问分期方案，另外投诉一下之前回复不及时'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M8", "issue": "客服回复不及时"\}\}

\{"query":"'user':'早上好，我想了解M7的内饰配置、官方售价，还想问问能不能上门试驾留联系方式'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M7"\}\}

\{"query":"'user':'你好，我对比了好几款车型，想问问M9顶配落地多少钱，有没有购车福利，怎么下单'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'哈喽，想问下预算30万入手M8宗师版合适吗，分期36期月供多少'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M8", "budget": "30万"\}\}

\{"query":"'user':'您好，我不想留私人电话，但是想了解新款M6的动力配置和最新售价'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M6", "lead\_refused": true\}\}

\{"query":"'user':'下午好，之前留过电话没人联系我，我想再问问M9的购车政策和全款价格'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M9", "issue": "留电话后无人联系"\}\}

\{"query":"'user':'你好，我想入手一台M8，想知道不同配置的差价，还想咨询置换购车方案'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'哈喽，咨询一下M7的续航配置，预算22万能不能落地，支持几期免息'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M7", "budget": "22万"\}\}

\{"query":"'user':'您好，我想修改之前预留的联系电话，同时了解新款M9的安全配置和报价'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'晚上好，想问下M8混动版的详细参数，全款和分期分别需要多少钱'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'你好，我准备购车，想了解车型配置、最新优惠，顺便留个电话预约咨询'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{\}\}

\{"query":"'user':'哈喽，投诉一下咨询无人应答，同时想问问M6新款的价格和空间配置'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M6", "issue": "咨询无人应答"\}\}

\{"query":"'user':'您好，我预算18万，不想留联系方式，想了解这个价位能入手的传祺车型配置和售价'", "primary\_intent": "greeting", "sub\_tasks": \["GREETING"\], "slots": \{"budget": "18万", "lead\_refused": true\}\}

## 二、primary\_intent: product\_inq（15条）

\{"query":"'user':'帮我详细介绍一下M9燃油版的动力、内饰、安全配置，顺便说说落地价大概多少'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'M8宗师版和尊贵版配置有什么区别，各自分期购车的月供费用是多少'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'我想了解新款M7的续航参数、智能座舱配置，预算20万能不能全款拿下这款车'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M7", "budget": "20万"\}\}

\{"query":"'user':'user':'之前问过价格，assistant':'已为你查询基础报价',user':'那M6新款的底盘和动力配置怎么样，有没有购车优惠'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M6"\}\}

\{"query":"'user':'想入手M9顶配车型，详细了解整车配置、质保政策，同时咨询下单购车流程'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'M8混动版的油耗、空间、智能配置如何，30万预算分期购买划算吗'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M8", "budget": "30万"\}\}

\{"query":"'user':'我想对比M7和M8的外观、内饰、动力配置，看看哪款性价比更高，落地更便宜'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{\}\}

\{"query":"'user':'新款M9有没有新增驾驶辅助配置，全款购买的最新优惠政策是什么'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'想了解M6家用版的空间和安全配置，预留电话预约到店看车并咨询报价'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M6"\}\}

\{"query":"'user':'投诉车型介绍不详细，麻烦完整讲解M8全系配置差异，以及各版本售价区间'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M8", "issue": "车型介绍不详细"\}\}

\{"query":"'user':'M7新能源版的电池配置、续航能力怎么样，支持多少期免息分期购车'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M7"\}\}

\{"query":"'user':'我不预留联系方式，麻烦告知新款全系车型的核心配置和入门售价'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"lead\_refused": true\}\}

\{"query":"'user':'想修改之前的咨询信息，重新了解M9运动版的专属配置和落地价格'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'M8商务版的座椅、影音配置适合商务使用吗，28万预算能否落地提车'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"model": "M8", "budget": "28万"\}\}

\{"query":"'user':'多轮咨询一直没得到完整配置解答，现在需要M6、M7、M8三款车型的详细参数和报价'", "primary\_intent": "product\_inq", "sub\_tasks": \["PRODUCT"\], "slots": \{"issue": "咨询未得到完整解答"\}\}

## 三、primary\_intent: price\_inq（15条）

\{"query":"'user':'M9顶配和次顶配全款落地分别多少钱，分期24期的月供和总利息是多少'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'预算25万，M8混动版能不能落地，现在购车有没有现金优惠和置换补贴'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M8", "budget": "25万"\}\}

\{"query":"'user':'user':'了解过配置了，assistant':'已讲解完毕',user':'那M7新能源版全款、分期报价分别是多少'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M7"\}\}

\{"query":"'user':'新款M6各版本官方售价和落地价汇总，我想直接下单购买，需要预留电话对接'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M6"\}\}

\{"query":"'user':'投诉报价不透明，麻烦清晰告知M8宗师版全款价格、分期方案及所有购车费用'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M8", "issue": "购车报价不透明"\}\}

\{"query":"'user':'30万预算入手M9，全款和免息分期哪个更划算，具体费用分别是多少'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M9", "budget": "30万"\}\}

\{"query":"'user':'我不方便留联系方式，麻烦告知M7燃油版最新落地价和购车优惠政策'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M7", "lead\_refused": true\}\}

\{"query":"'user':'修改之前的预算咨询，重新查询M8尊贵版36期分期的总费用和每月月供'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'M6家用版和运动版差价多少，各自全款落地费用包含哪些项目'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M6"\}\}

\{"query":"'user':'想购车，先咨询M9全系车型最新售价、优惠力度，再确认下单流程'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'22万预算能买到哪款传祺车型，对应车型的配置和落地价格详细说一下'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"budget": "22万"\}\}

\{"query":"'user':'之前咨询价格一直没有精准回复，现在需要M8混动版精准全款和分期报价'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M8", "issue": "价格咨询回复不精准"\}\}

\{"query":"'user':'M7新款购车首付需要多少，月供多少，总落地价相比全款贵多少'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M7"\}\}

\{"query":"'user':'预留手机号13898765432，咨询M9顶配专属购车优惠和最终落地价格'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{"model": "M9", "phone": "13898765432"\}\}

\{"query":"'user':'对比M6和M7的入门版售价、落地成本，结合配置差异看看哪个性价比更高'", "primary\_intent": "price\_inq", "sub\_tasks": \["PRICE"\], "slots": \{\}\}

## 四、primary\_intent: purchase（15条）

\{"query":"'user':'我确定入手M8宗师版，想了解详细下单流程，同时咨询全款购车优惠和赠品政策'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'打算分期购买M9顶配，需要提交什么资料，首付比例多少，预留电话对接购车事宜'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'user':'了解完价格配置了，assistant':'已为你全部解答',user':'那我现在怎么下单购车，最快多久能提车'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{\}\}

\{"query":"'user':'20万预算全款购入M7家用版，麻烦告知购车流程、付款方式和提车周期'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M7", "budget": "20万"\}\}

\{"query":"'user':'投诉提车流程不清晰，现在明确要购买M6新款，需要完整的线上线下下单步骤'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M6", "issue": "购车提车流程不清晰"\}\}

\{"query":"'user':'我不方便预留个人电话，麻烦单独告知M8混动版的自助下单购车流程'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M8", "lead\_refused": true\}\}

\{"query":"'user':'修改之前的购车意向，现在确定分期购买M9运动版，重新对接购车方案'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'预留13612345678联系方式，想要批量了解M系列车型购车政策，准备近期下单'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"phone": "13612345678"\}\}

\{"query":"'user':'想入手新能源M7，咨询免息分期购车方案、下单流程以及落地总费用'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M7"\}\}

\{"query":"'user':'对比完所有车型，最终选择M8尊贵版，麻烦指导全款下单和后续上牌流程'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'28万预算购车，优先M9入门版，咨询下单定金金额和尾款支付方式'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M9", "budget": "28万"\}\}

\{"query":"'user':'之前咨询购车无人跟进，现在再次申请购买M6新款，安排专人对接下单事宜'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M6", "issue": "购车咨询无人跟进"\}\}

\{"query":"'user':'想了解置换购车流程，打算用旧车置换M8混动版，咨询置换补贴和下单要求'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'不接受电话回访，只要文字告知M7燃油版完整的购车下单和提车注意事项'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{"model": "M7", "lead\_refused": true\}\}

\{"query":"'user':'确定近期购车，需要汇总M全系车型最新购车优惠、分期政策和快速下单渠道'", "primary\_intent": "purchase", "sub\_tasks": \["PURCHASE", "LEAD\_CAPTURE"\], "slots": \{\}\}

## 五、primary\_intent: complaint（15条）

\{"query":"'user':'多次咨询M9价格和配置问题，一直没有客服精准回复，服务态度很差'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M9", "issue": "多次咨询无精准回复、服务态度差"\}\}

\{"query":"'user':'user':'咨询购车问题',assistant':'未回复',user':'等待半小时无人搭理，投诉客服响应不及时'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"issue": "客服响应不及时、无人对接咨询"\}\}

\{"query":"'user':'之前预留电话咨询M8分期方案，至今没有工作人员联系，诉求得不到解决'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M8", "issue": "预留电话后无人跟进回访"\}\}

\{"query":"'user':'咨询的M7车型配置信息前后矛盾，报价忽高忽低，信息解答不专业'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M7", "issue": "车型配置、报价信息解答矛盾、不专业"\}\}

\{"query":"'user':'申请修改咨询信息后，一直没有更新对接，购车咨询流程拖沓'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"issue": "修改咨询信息后无人对接、流程拖沓"\}\}

\{"query":"'user':'询问M6购车优惠和下单方式，多次提问均被敷衍，没有完整解答'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M6", "issue": "咨询购车问题被敷衍、解答不完整"\}\}

\{"query":"'user':'明明标注有免息分期政策，咨询时被告知没有，虚假宣传购车福利'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"issue": "购车分期政策虚假宣传"\}\}

\{"query":"'user':'到店咨询M8混动版配置价格，工作人员讲解潦草，关键信息隐瞒不告知'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M8", "issue": "线下咨询讲解潦草、隐瞒关键信息"\}\}

\{"query":"'user':'拒绝电话回访后，仍然频繁收到推销电话，骚扰个人生活'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"issue": "拒绝回访后仍被电话骚扰", "lead\_refused": true\}\}

\{"query":"'user':'预约M9试驾迟迟没有安排，购车咨询全程无人跟进，体验极差'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M9", "issue": "试驾预约未安排、购车咨询无人跟进"\}\}

\{"query":"'user':'不同客服给出的M7落地价不一致，报价混乱，无法参考购车预算'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M7", "issue": "车型落地报价混乱、标准不统一"\}\}

\{"query":"'user':'咨询置换购车流程一周，始终没有专人对接，问题悬而未决'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"issue": "置换购车咨询长期无人对接解决"\}\}

\{"query":"'user':'查询M6新款核心配置，多次咨询都只回复基础信息，解答不全面'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"model": "M6", "issue": "车型配置解答片面、不全面"\}\}

\{"query":"'user':'预留手机号咨询购车，信息被泄露，频繁收到各类汽车推销短信'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"phone": "13912345678", "issue": "预留联系方式后信息泄露"\}\}

\{"query":"'user':'咨询30万预算购车方案，客服推荐车型不符需求，专业度不足'", "primary\_intent": "complaint", "sub\_tasks": \["COMPLAINT"\], "slots": \{"budget": "30万", "issue": "车型推荐不符合用户需求、专业度低"\}\}

## 六、primary\_intent: contact\_give（15条）

\{"query":"'user':'我的电话是13511223344，麻烦联系我详细讲解M9顶配的分期购车方案和落地价'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13511223344", "model": "M9"\}\}

\{"query":"'user':'预留手机号13722334455，想预约到店看M8混动版，同步咨询购车优惠'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13722334455", "model": "M8"\}\}

\{"query":"'user':'user':'想深度咨询购车事宜',assistant':'可预留联系方式',user':'13833445566，麻烦专人对接'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13833445566"\}\}

\{"query":"'user':'联系方式13944556677，预算22万，帮我匹配合适的传祺车型并报价'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13944556677", "budget": "22万"\}\}

\{"query":"'user':'留下电话13655667788，投诉之前咨询服务问题，同时重新对接M7购车方案'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13655667788", "model": "M7", "issue": "前期咨询服务不佳"\}\}

\{"query":"'user':'手机号13466778899，需要详细了解M8宗师版全款、分期全部购车费用'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13466778899", "model": "M8"\}\}

\{"query":"'user':'预留13377889900，想申请M9新款试驾，同步咨询下单提车流程'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13377889900", "model": "M9"\}\}

\{"query":"'user':'电话13288990011，28万预算购车，麻烦推荐高配置车型并核算落地价'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13288990011", "budget": "28万"\}\}

\{"query":"'user':'13199001122，想咨询旧车置换M6新款的补贴政策和分期方案'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13199001122", "model": "M6"\}\}

\{"query":"'user':'预留手机号13000112233，针对之前报价疑问，需要专人电话解答'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "13000112233", "issue": "购车报价存在疑问"\}\}

\{"query":"'user':'我的号码12911223344，想批量了解M7、M8全系配置差异和最新优惠'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "12911223344"\}\}

\{"query":"'user':'12822334455，近期打算全款购车，麻烦整理详细购车方案对接我'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "12822334455"\}\}

\{"query":"'user':'预留12733445566，咨询新能源车型续航配置和免息分期政策'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "12733445566"\}\}

\{"query":"'user':'电话12644556677，修改之前的咨询需求，重新对接M9运动版购车事宜'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "12644556677", "model": "M9"\}\}

\{"query":"'user':'12555667788，投诉无人跟进咨询，留下联系方式要求尽快回访解决问题'", "primary\_intent": "contact\_give", "sub\_tasks": \["LEAD\_CAPTURE"\], "slots": \{"phone": "12555667788", "issue": "咨询问题长期无人跟进解决"\}\}

## 七、primary\_intent: contact\_no（15条）

\{"query":"'user':'我绝对不方便预留任何电话信息，麻烦直接文字解答M8配置和落地价问题'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M8", "lead\_refused": true\}\}

\{"query":"'user':'无需电话回访，不要记录我的联系方式，直接告知M9分期购车的详细费用'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M9", "lead\_refused": true\}\}

\{"query":"'user':'user':'需要咨询购车问题',assistant':'可预留电话回访',user':'不用留联系方式，文字回复即可'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"lead\_refused": true\}\}

\{"query":"'user':'拒绝所有电话联系和信息登记，麻烦讲解M7新能源版的续航和购车优惠'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M7", "lead\_refused": true\}\}

\{"query":"'user':'之前被电话骚扰过，坚决不留联系方式，直接解答M6全款购车流程和报价'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M6", "lead\_refused": true, "issue": "曾被购车咨询电话骚扰"\}\}

\{"query":"'user':'不需要专人电话对接，自行了解即可，告知25万预算能入手的车型配置'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"budget": "25万", "lead\_refused": true\}\}

\{"query":"'user':'拒绝预留手机号，直接文字回复M8混动版置换购车的补贴政策'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M8", "lead\_refused": true\}\}

\{"query":"'user':'不用记录我的任何联系信息，解答M9顶配和次顶配的配置差价问题'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M9", "lead\_refused": true\}\}

\{"query":"'user':'反感电话推销，坚决不留联系方式，告知新款车型的官方购车政策'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"lead\_refused": true, "issue": "反感购车电话推销"\}\}

\{"query":"'user':'无需回访跟进，直接回复M7家用版的落地价和分期月供明细'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M7", "lead\_refused": true\}\}

\{"query":"'user':'不提供任何个人联系方式，麻烦解决之前车型解答不清晰的投诉问题'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"lead\_refused": true, "issue": "前期车型解答不清晰"\}\}

\{"query":"'user':'仅接受文字咨询，拒绝电话沟通，对比M6和M8的性价比与落地成本'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"lead\_refused": true\}\}

\{"query":"'user':'不用登记我的信息，直接告知免息分期购车的申请条件和流程'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"lead\_refused": true\}\}

\{"query":"'user':'杜绝所有电话回访，详细讲解M8宗师版的安全配置和质保服务'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"model": "M8", "lead\_refused": true\}\}

\{"query":"'user':'私人信息不便透露，直接解答30万预算分期购车的最优方案'", "primary\_intent": "contact\_no", "sub\_tasks": \["CONTACT\_NO"\], "slots": \{"budget": "30万", "lead\_refused": true\}\}

## 八、primary\_intent: contact\_fix（15条）

\{"query":"'user':'之前预留的13511223344号码作废，修改新手机号13999887766，继续对接M9购车咨询'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "13999887766", "model": "M9"\}\}

\{"query":"'user':'更换预留联系方式，原13722334455改为13888776655，重新咨询M8分期价格'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "13888776655", "model": "M8"\}\}

\{"query":"'user':'user':'预留号码13611112222咨询购车',assistant':'已登记',user':'请修改我的联系电话'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{\}\}

\{"query":"'user':'修改登记信息，取消旧号码绑定，新手机号13555443322，对接M7车型配置咨询'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "13555443322", "model": "M7"\}\}

\{"query":"'user':'之前预留电话无人联系，现更换号码13444332211，重新申请购车专人对接'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "13444332211", "issue": "旧号码预留后无人联系"\}\}

\{"query":"'user':'更新个人咨询联系方式，原13333221100改为13222110099，咨询M6落地价'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "13222110099", "model": "M6"\}\}

\{"query":"'user':'修改预留信息，更换手机号后，继续跟进我的20万预算购车方案咨询'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"budget": "20万"\}\}

\{"query":"'user':'旧号码停用，登记新电话13111009988，重新对接M8混动版置换购车事宜'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "13111009988", "model": "M8"\}\}

\{"query":"'user':'修正之前预留的错误手机号，正确号码13000998877，继续咨询M9顶配优惠'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "13000998877", "model": "M9"\}\}

\{"query":"'user':'更换回访电话，避免之前的骚扰问题，新号12999887766，重新对接咨询'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "12999887766", "issue": "旧号码存在推销骚扰问题"\}\}

\{"query":"'user':'更新联系方式12888776655，继续跟进我之前的车型配置对比咨询需求'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "12888776655"\}\}

\{"query":"'user':'修改购车咨询预留电话，原号注销，新号12777665544，咨询分期月供'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "12777665544"\}\}

\{"query":"'user':'调整个人登记信息，更换手机号后，重新预约M7新款试驾服务'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"model": "M7"\}\}

\{"query":"'user':'修正预留号码错误问题，正确电话12666554433，继续处理我的购车投诉问题'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "12666554433", "issue": "前期购车咨询问题未解决"\}\}

\{"query":"'user':'更新回访联系方式，12555443322，重新核算我30万预算的购车落地费用'", "primary\_intent": "contact\_fix", "sub\_tasks": \["CONTACT\_FIX"\], "slots": \{"phone": "12555443322", "budget": "30万"\}\}

## 九、primary\_intent: chitchat（15条）

\{"query":"'user':'今天天气挺好的，顺便问问新款M9的外观颜值怎么样，落地价格贵不贵'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'最近一直在看车，感觉传祺车型口碑不错，想了解M8适合家用还是商务使用'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'user':'平时开车比较多',assistant':'了解',user':'那M7混动版油耗高不高，分期买划算吗'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M7"\}\}

\{"query":"'user':'身边朋友都推荐传祺汽车，我预算20万，有没有性价比高的车型推荐和报价'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"budget": "20万"\}\}

\{"query":"'user':'感觉现在新能源汽车越来越普及，想问问M系列新能源车型的续航和购车政策'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{\}\}

\{"query":"'user':'纠结全款还是分期买车，想听听建议，顺便看看M6的最新售价和配置'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M6"\}\}

\{"query":"'user':'之前咨询体验一般，不过车型还是挺喜欢的，再问问M9的购车优惠活动'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M9", "issue": "前期咨询体验一般"\}\}

\{"query":"'user':'打算年底换车，不想留电话打扰，简单了解下M8宗师版的核心优势和落地价'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M8", "lead\_refused": true\}\}

\{"query":"'user':'闲聊一下传祺汽车的口碑，对比下M7和M8哪款更适合日常通勤，性价比更高'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{\}\}

\{"query":"'user':'感觉燃油车性价比稳定，想问问25万预算入手传祺车型的整体体验怎么样'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"budget": "25万"\}\}

\{"query":"'user':'和朋友闲聊购车心得，想了解M6新款的受众人群和日常用车优势'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M6"\}\}

\{"query":"'user':'最近油价波动大，想聊聊混动车型优势，咨询M8混动版的日常用车成本'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M8"\}\}

\{"query":"'user':'简单唠唠购车选择，30万预算选传祺顶配车型是否值得，有什么优势'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"budget": "30万"\}\}

\{"query":"'user':'听说传祺售后口碑不错，闲聊问问M9的售后保障和基础用车福利'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"model": "M9"\}\}

\{"query":"'user':'日常随便问问，不想登记信息，了解下传祺主流车型的市场口碑'", "primary\_intent": "chitchat", "sub\_tasks": \["GREETING"\], "slots": \{"lead\_refused": true\}\}

> （注：部分内容可能由 AI 生成）
