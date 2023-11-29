#!/bin/bash
BASE_DIR=/workspace
VLLM_DIR=$BASE_DIR/vllm-private
GRAD_DIR=$BASE_DIR/gradlib
RPD_DIR=/workspace/rocmProfileData
#MODEL=/data/llama2-70b-chat
MODEL=/data/llama-2-13b-chat-hf
MODEL_SIZE=`echo $MODEL | sed 's/.*\(.[0-9][bB]\).*/\1/'`


#Flag to use Triton Flash Attention vs CK
export VLLM_USE_TRITON=1

#Delete tuned gemms before running.
DELETE_TUNED_CSV=1

#Flag to disable MSCCL
#export RCCL_MSCCL_ENABLE=0

#HIPGraph performance flags
export HIP_FORCE_DEV_KERNARG=1
export DEBUG_CLR_GRAPH_PACKET_CAPTURE=1

#Enable full decoder graph mode
HIP_GRAPH=--use-cuda-graph

#Use top of tree build of RCCL
export LD_LIBRARY_PATH=/workspace/rccl/build/

#Enable either flag to create a profile trace (rocprof, or rocpd)
#RPD_PROFILE="--profile"
#ROCPROF_PROFILE="rocprof --hip-trace"

#TP="1 2 4 8"
#INPUT_LEN="512 1024 2048 3072"
TP=1
GEN_LEN="1 32"
INPUT_LEN="512 1024 2048 3072 4096 6144 8192 16384"
ITER=10

for tp in $TP;
do
    echo "tuned_gemm_csv: ./tuned_tp$tp.csv" > $VLLM_DIR/tuned_perf_tp$tp.yaml
    tuned_file=$VLLM_DIR/tuned_tp$tp.csv
    if [[ $DELETE_TUNED_CSV == 1 || ! -f $VLLM_DIR/tuned_tp$tp.csv ]];
    then
            rm -rf $tuned_file
            echo "INFO: Generating Tuned Gemm configs"
            cd $GRAD_DIR
            python gemm_tuner.py --model_dir $MODEL --output $tuned_file --tp $tp
    fi
    export VLLM_PERF_YAML=./tuned_perf_tp$tp.yaml

    echo "================================= TUNED GEMMS  $tuned_file ==============================================="
    cat $tuned_file

    cd $VLLM_DIR
    for gen_len in $GEN_LEN;
    do
        for input_len in $INPUT_LEN;
        do
            if [[ -v RPD_PROFILE ]] ;
            then
                rm /workspace/trace.rpd
                python -m rocpd.schema --create /workspace/trace.rpd
            fi
            echo "================================= RUNNING $MODEL $input_len $gen_len ==============================================="
            $ROCPROF_PROFILE torchrun --standalone --nnodes=1 --nproc-per-node=$tp benchmarks/benchmark_latency.py --model $MODEL  --batch-size 1 --input-len $input_len --output-len $gen_len \
            --tensor-parallel-size $tp --num-iters $ITER $HIP_GRAPH $RPD_PROFILE
            if [[ -v ROCPROF_PROFILE ]] ;
            then
                TRACE_FILE=$BASE_DIR/trace_${MODEL_SIZE}_${input_len}_${gen_len}.json
                echo "INFO: Creating Trace JSON file $TRACE_FILE"
                mv $VLLM_DIR/results.json $TRACE_FILE
            fi
            if [[ -v RPD_PROFILE ]] ;
            then
                TRACE_FILE=$BASE_DIR/trace_${MODEL_SIZE}_${input_len}_${gen_len}.json
                echo "INFO: Creating Trace JSON file $TRACE_FILE"
                python $RPD_DIR/tools/rpd2tracing.py --format object $BASE_DIR/trace.rpd $TRACE_FILE
            fi
        done
    done
done
