"""
EchoMind 意图识别模型训练脚本 — 在 Google Colab 免费 T4 GPU 上运行。

使用方式:
  1. 打开 https://colab.research.google.com
  2. 把本文件的内容复制进去（或上传）
  3. 运行「安装依赖」→「上传数据」→「训练」→「下载 GGUF」
  4. 把下载的 GGUF 放到本地电脑
  5. ollama create echomind-intent -f Modelfile

输出: echomind-intent.gguf（~1GB，可直接用于 Ollama 推理）
"""

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 1: 安装依赖（运行一次，约 3 分钟）
# ═══════════════════════════════════════════════════════════════════════════════

# %pip install unsloth
# %pip install "unsloth[colab] @ git+https://github.com/unslothai/unsloth.git"
# %pip install --upgrade --quiet pillow --no-cache-dir
# 注意: 安装完后 Runtime → Restart runtime 重启运行时

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 2: 导入依赖
# ═══════════════════════════════════════════════════════════════════════════════

# import json
# import os
#
# import torch
# from datasets import Dataset, load_dataset
# from transformers import TrainingArguments
# from trl import SFTTrainer
# from unsloth import FastLanguageModel, is_bfloat16_supported

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 3: 上传数据文件
# ═══════════════════════════════════════════════════════════════════════════════

# from google.colab import files
# print("请选择 intent_train_alpaca.json 上传")
# uploaded = files.upload()
#
# # 写入 Colab 环境
# for name in uploaded.keys():
#     print(f"已上传: {name} ({len(uploaded[name])} bytes)")
#
# # 检查数据
# with open("intent_train_alpaca.json", "r") as f:
#     data = json.load(f)
# print(f"数据条数: {len(data)}")
# print(f"样例: {json.dumps(data[0], ensure_ascii=False)[:200]}")

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 4: 加载模型 + 加 LoRA
# ═══════════════════════════════════════════════════════════════════════════════

# MAX_SEQ_LENGTH = 512
#
# model, tokenizer = FastLanguageModel.from_pretrained(
#     model_name="Qwen/Qwen2.5-1.5B-Instruct",    # ← 1.5B，内存约 2GB
#     # model_name="Qwen/Qwen2.5-0.5B-Instruct",   # ← 更小的选择，~1GB，精度略低
#     max_seq_length=MAX_SEQ_LENGTH,
#     dtype=None,
#     load_in_4bit=True,   # 4bit 量化，T4 上省显存
# )
#
# # 加 LoRA adapter
# model = FastLanguageModel.get_peft_model(
#     model,
#     r=16,
#     target_modules=[
#         "q_proj", "k_proj", "v_proj", "o_proj",
#         "gate_proj", "up_proj", "down_proj",
#     ],
#     lora_alpha=16,
#     lora_dropout=0,
#     bias="none",
#     use_gradient_checkpointing="unsloth",
#     random_state=42,
# )
#
# print("模型加载完成，可训练参数:", model.print_trainable_parameters())

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 5: 准备训练数据（Alpaca → 对话格式）
# ═══════════════════════════════════════════════════════════════════════════════

# def format_func(examples):
#     """将 Alpaca 格式转为 Qwen 对话格式。"""
#     texts = []
#     for instr, inp, out in zip(examples["instruction"],
#                                 examples["input"],
#                                 examples["output"]):
#         messages = [
#             {"role": "system", "content": instr},
#             {"role": "user", "content": inp},
#             {"role": "assistant", "content": out},
#         ]
#         text = tokenizer.apply_chat_template(
#             messages, tokenize=False, add_generation_prompt=False
#         )
#         texts.append(text)
#     return {"text": texts}
#
#
# # 加载数据集
# dataset = load_dataset("json", data_files="intent_train_alpaca.json", split="train")
#
# # 格式转换
# dataset = dataset.map(format_func, batched=True)
#
# # 拆 9:1 训练/验证
# split_dataset = dataset.train_test_split(test_size=0.1, seed=42)
# train_data = split_dataset["train"]
# eval_data = split_dataset["test"]
#
# print(f"训练集: {len(train_data)} 条")
# print(f"验证集: {len(eval_data)} 条")
# print(f"\n格式化后样例:\n{train_data[0]['text'][:300]}...")

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 6: 训练（约 5-10 分钟）
# ═══════════════════════════════════════════════════════════════════════════════

# trainer = SFTTrainer(
#     model=model,
#     tokenizer=tokenizer,
#     train_dataset=train_data,
#     eval_dataset=eval_data,
#     dataset_text_field="text",
#     max_seq_length=MAX_SEQ_LENGTH,
#     args=TrainingArguments(
#         output_dir="./echomind-intent",
#         per_device_train_batch_size=4,
#         per_device_eval_batch_size=4,
#         gradient_accumulation_steps=4,
#         warmup_steps=5,
#         num_train_epochs=5,
#         learning_rate=2e-4,
#         fp16=not is_bfloat16_supported(),
#         bf16=is_bfloat16_supported(),
#         logging_steps=5,
#         evaluation_strategy="steps",
#         eval_steps=20,
#         save_steps=50,
#         save_total_limit=2,
#         report_to="none",
#         lr_scheduler_type="cosine",
#     ),
# )
#
# # 开始训练
# trainer.train()
#
# # 保存 adapter 权重
# model.save_pretrained("./echomind-intent-adapter")
# tokenizer.save_pretrained("./echomind-intent-adapter")
# print("✅ LoRA adapter 已保存")

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 7: 导出 GGUF（Ollama 可直接加载）
# ═══════════════════════════════════════════════════════════════════════════════

# # 合并 LoRA 权重 → 导出 GGUF
# model.save_pretrained_gguf(
#     "./echomind-intent-gguf",
#     tokenizer,
#     quantization_method="q4_k_m",  # 4bit GGUF，平衡体积和精度
# )
#
# # 确认导出成功
# import os
# files = [f for f in os.listdir("./echomind-intent-gguf") if f.endswith(".gguf")]
# print(f"GGUF 文件: {files}")
#
# for f in files:
#     size = os.path.getsize(f"./echomind-intent-gguf/{f}")
#     print(f"  {f}: {size / 1024 / 1024:.1f} MB")

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 8: 下载到本地
# ═══════════════════════════════════════════════════════════════════════════════

# from google.colab import files
# gguf_path = [f for f in os.listdir("./echomind-intent-gguf") if f.endswith(".gguf")][0]
# files.download(f"./echomind-intent-gguf/{gguf_path}")
#
# print(f"⬇️ 下载中: {gguf_path}")
# print("下载完成后，在本地执行:")
# print(f"  echo 'FROM {gguf_path}' > Modelfile")
# print(f"  ollama create echomind-intent -f Modelfile")
# print(f"  ollama run echomind-intent")
