"""Mandatory CUDA for MLP experiments, with a diagnostic and a real training-step check."""
import os
import platform
import shutil

# Deterministic cuBLAS must be configured before CUDA initialises.
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')


class CudaUnavailable(SystemExit):
    pass


def diagnostic():
    import torch
    info = {'torch': torch.__version__, 'torch_cuda_build': torch.version.cuda, 'cuda_available': torch.cuda.is_available(),
            'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES'), 'nvidia_smi_on_path': bool(shutil.which('nvidia-smi')),
            'python': platform.python_version()}
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        info.update(device=torch.cuda.get_device_name(0), total_vram_gib=round(props.total_memory / 2 ** 30, 2),
                    capability=f'{props.major}.{props.minor}', cudnn=torch.backends.cudnn.version())
    return info


def require_cuda():
    """Return the CUDA device or stop with the reason; there is no CPU fallback for MLP runs."""
    import torch
    info = diagnostic()
    if not info['cuda_available']:
        raise CudaUnavailable(f'CUDA is required for MLP training and is unavailable: {info}. '
                              'Install a CUDA build of PyTorch and check the NVIDIA driver.')
    torch.use_deterministic_algorithms(True)
    return torch.device('cuda:0'), info


def training_step_check(device):
    """Run forward, backward and an optimizer step; confirm every tensor involved lives on CUDA."""
    import torch
    torch.manual_seed(0)
    network = torch.nn.Sequential(torch.nn.Linear(8, 16), torch.nn.ReLU(), torch.nn.Linear(16, 1)).to(device)
    optimizer = torch.optim.AdamW(network.parameters(), lr=1e-3)
    x = torch.randn(64, 8, device=device)
    y = torch.randn(64, device=device)
    before = [p.detach().clone() for p in network.parameters()]
    output = network(x).squeeze(1)
    loss = torch.nn.functional.mse_loss(output, y)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    torch.cuda.synchronize(device)
    checks = {'forward_on_cuda': output.is_cuda, 'loss_on_cuda': loss.is_cuda,
              'grads_on_cuda': all(p.grad is not None and p.grad.is_cuda for p in network.parameters()),
              'params_on_cuda': all(p.is_cuda for p in network.parameters()),
              'optimizer_state_on_cuda': all(s['exp_avg'].is_cuda for s in optimizer.state.values()),
              'parameters_changed': any(not torch.equal(a, b) for a, b in zip(before, network.parameters()))}
    if not all(checks.values()):
        raise CudaUnavailable(f'Training step did not run fully on CUDA: {checks}')
    return checks
