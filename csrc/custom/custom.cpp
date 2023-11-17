#include <ATen/cuda/CUDAContext.h>
#include <torch/extension.h>
#include <pybind11/pybind11.h>
#include <cuda_runtime.h>

namespace py = pybind11;

// declare templates for front (cpp) and back (cuda) sides of function:
//template <typename T>
void LLGemm1(void *in_a, void *in_b, void *out_c, const int M, const int K, cudaStream_t stream);

//template <typename T>
void LLMM1(at::Tensor in_a, at::Tensor in_b, at::Tensor out_c) {
      int M = in_a.size(0);
      int K = in_a.size(1);
        //if (N != in_b.numel())
        //        throw std::invalid_argument("Size mismatch A.numel(): " + std::to_string(in_a.numel())
        //                          + ", B.numel(): " + std::to_string(in_b.numel()));

          //out_c.resize_({N});

          // call the kernel function...
           LLGemm1(in_a.data_ptr(), in_b.data_ptr(),
                             out_c.data_ptr(), M, K, at::cuda::getCurrentCUDAStream());
}

// instantiate the CPP template for T=float:
//template void AddGPU<float>(at::Tensor in_a, at::Tensor in_b, at::Tensor out_c);


void MMGPUKernel(float *in_a, float *in_b, float *out_c, 
        int numARows, int numAColumns,
        int numBRows, int numBColumns,
        int numCRows, int numCColumns, 
        cudaStream_t stream);


void MMCustomGPU(at::Tensor in_a, at::Tensor in_b, at::Tensor out_c) {
        auto matA_sizes { in_a.sizes() };
        auto matB_sizes { in_b.sizes() };
        auto matO_sizes { out_c.sizes() };
        MMGPUKernel(in_a.data_ptr<float>(), in_b.data_ptr<float>(), out_c.data_ptr<float>(), 
            matA_sizes[0], matA_sizes[1],
            matB_sizes[0], matB_sizes[1],
            matO_sizes[0], matO_sizes[1],
        at::cuda::getCurrentCUDAStream());
}

// declare the extension module with the AddGPU function:
PYBIND11_MODULE(TORCH_EXTENSION_NAME, m){
      m.doc() = "pybind11 example plugin";
        m.def("LLMM1", &LLMM1);
//m.def("MMCustomGPU", &MMCustomGPU);
}
