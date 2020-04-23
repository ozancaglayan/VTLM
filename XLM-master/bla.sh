#!/bin/bash

source /home/menekse/virtualenvs/torch_env/bin/activate
export NGPU=3;

python -m torch.distributed.launch --nproc_per_node=$NGPU --nnodes=1 --node_rank=0 \
  --master_addr="193.140.236.52" --master_port=8088 train.py --exp_name xlm_en_de_tlm \
  --dump_path /data/menekse/dumped --data_path /data/shared/ConceptualCaptions/XLM_data/50k \
  --lgs 'en-de' --clm_steps '' --mlm_steps 'en-de' --emb_dim 512 --n_layers 6 --n_heads 8 \
  --dropout 0.1 --attention_dropout 0.1 --gelu_activation true --batch_size 32 --bptt 256 \
  --optimizer adam,lr=0.0001 --epoch_size 300000 --max_epoch 100000 \
  --validation_metrics valid_en_de_mlm_ppl --stopping_criterion valid_en_de_mlm_ppl,25 \
  --fp16 false --reload_model /data/menekse/dumped/xlm_en_de_tlm/3094/best-valid_en_de_mlm_ppl.pth \
  --save_periodic 2
