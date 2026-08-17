"""Data-driven catalogue of every ``llama-server`` command-line option.

The catalogue is **data, not control flow** (the same principle as the backend
table in :mod:`llamagui.models`): one :class:`ServerArg` row per option, in the
order ``llama-server --help`` prints them. The GUI renders the whole grid from
this table, the launch code serialises user values to CLI tokens from it, and
:mod:`scripts.check_server_args` diffs it against a real binary's ``--help`` so
a new nightly build can never silently outrun the app.

Reference snapshot: ``docs/reference/llama-server-help.txt``
(``llama-server`` b10488, 0.1.2-dev, commit 9d77fa172).

Four flags are *dedicated* — they are owned by long-standing ``AppConfig``
fields (``host``, ``port``, ``ctx_size``, ``n_gpu_layers``) so the rest of the
engine (port probing, stop, dashboard) keeps a single source of truth for
them. They still appear in the grid, bound to those fields.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field


class ArgKind(enum.StrEnum):
    """Editor kind the GUI uses for one option."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    CHOICE = "choice"
    PATH = "path"


#: Section headers exactly as printed by ``llama-server --help``.
SECTIONS = ("common", "sampling", "speculative", "server")

#: Flags owned by dedicated AppConfig fields (see module docstring).
DEDICATED_FLAGS = frozenset({"--host", "--port", "--ctx-size", "--n-gpu-layers"})

_TRUE = frozenset({"on", "true", "1", "yes", "enabled"})
_FALSE = frozenset({"off", "false", "0", "no", "disabled"})


@dataclass(frozen=True)
class ServerArg:
    """One ``llama-server`` option.

    ``flag`` is the canonical form emitted on the command line (the long form
    when one exists). ``aliases`` are accepted alternative spellings used by
    the GUI/CLI for lookups. ``negated`` is the disabling flag for two-state
    booleans (``--perf`` / ``--no-perf``); a flag with only a negative form
    (``--no-host``) stores it as the canonical flag.
    """

    flag: str
    section: str
    kind: ArgKind
    help: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    choices: tuple[str, ...] = field(default_factory=tuple)
    default: str = ""
    negated: str | None = None
    env: str = ""
    #: One-shot flag (prints something and exits) — never launchable.
    volatile: bool = False
    #: The app always supplies this flag itself (the model file).
    app_managed: bool = False
    #: PATH kind: the browse dialog picks a directory instead of a file.
    is_dir: bool = False
    deprecated: bool = False


def _choice(*values: str) -> tuple[str, ...]:
    return values


#: KV cache data types accepted by the ``-ctk``/``-ctv`` family.
_KV_TYPES = _choice(
    "f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"
)


# ─── common params ──────────────────────────────────────────────────────────

SERVER_ARGS: tuple[ServerArg, ...] = (
    ServerArg(
        "--help",
        "common",
        ArgKind.BOOL,
        "Print usage and exit.",
        aliases=("-h", "--usage"),
        volatile=True,
    ),
    ServerArg(
        "--version",
        "common",
        ArgKind.BOOL,
        "Show version and build info.",
        volatile=True,
    ),
    ServerArg(
        "--cache-list",
        "common",
        ArgKind.BOOL,
        "Show list of models in cache.",
        aliases=("-cl",),
        volatile=True,
    ),
    ServerArg(
        "--completion-bash",
        "common",
        ArgKind.BOOL,
        "Print a source-able bash completion script.",
        volatile=True,
    ),
    ServerArg(
        "--threads",
        "common",
        ArgKind.INT,
        "CPU threads for generation. -1 = auto.",
        aliases=("-t",),
        default="-1",
        env="LLAMA_ARG_THREADS",
    ),
    ServerArg(
        "--threads-batch",
        "common",
        ArgKind.INT,
        "CPU threads for batch / prompt processing.",
        aliases=("-tb",),
        default="same as --threads",
    ),
    ServerArg(
        "--cpu-mask",
        "common",
        ArgKind.STRING,
        "CPU affinity mask, arbitrary-length hex.",
        aliases=("-C",),
        default="",
    ),
    ServerArg(
        "--cpu-range",
        "common",
        ArgKind.STRING,
        "Range of CPUs for affinity, e.g. 0-7.",
        aliases=("-Cr",),
    ),
    ServerArg(
        "--cpu-strict",
        "common",
        ArgKind.CHOICE,
        "Use strict CPU placement.",
        choices=_choice("0", "1"),
        default="0",
    ),
    ServerArg(
        "--prio",
        "common",
        ArgKind.INT,
        "Process/thread priority: low(-1), normal(0), medium(1), high(2), realtime(3).",
        default="0",
    ),
    ServerArg(
        "--poll",
        "common",
        ArgKind.INT,
        "Polling level to wait for work (0 = no polling), 0..100.",
        default="50",
    ),
    ServerArg(
        "--cpu-mask-batch",
        "common",
        ArgKind.STRING,
        "CPU affinity mask for batch processing.",
        aliases=("-Cb",),
        default="same as --cpu-mask",
    ),
    ServerArg(
        "--cpu-range-batch",
        "common",
        ArgKind.STRING,
        "Range of CPUs for batch affinity.",
        aliases=("-Crb",),
    ),
    ServerArg(
        "--cpu-strict-batch",
        "common",
        ArgKind.CHOICE,
        "Strict CPU placement for batch processing.",
        choices=_choice("0", "1"),
        default="same as --cpu-strict",
    ),
    ServerArg(
        "--prio-batch",
        "common",
        ArgKind.INT,
        "Batch process/thread priority: 0-normal..3-realtime.",
        default="0",
    ),
    ServerArg(
        "--poll-batch",
        "common",
        ArgKind.INT,
        "Polling to wait for batch work.",
        default="same as --poll",
    ),
    ServerArg(
        "--ctx-size",
        "common",
        ArgKind.INT,
        "Prompt context size. 0 = loaded from model.",
        aliases=("-c",),
        default="0",
        env="LLAMA_ARG_CTX_SIZE",
    ),
    ServerArg(
        "--n-predict",
        "common",
        ArgKind.INT,
        "Tokens to predict. -1 = infinity.",
        aliases=("-n", "--predict"),
        default="-1",
        env="LLAMA_ARG_N_PREDICT",
    ),
    ServerArg(
        "--batch-size",
        "common",
        ArgKind.INT,
        "Logical maximum batch size.",
        aliases=("-b",),
        default="2048",
        env="LLAMA_ARG_BATCH",
    ),
    ServerArg(
        "--ubatch-size",
        "common",
        ArgKind.INT,
        "Physical maximum batch size.",
        aliases=("-ub",),
        default="512",
        env="LLAMA_ARG_UBATCH",
    ),
    ServerArg(
        "--keep",
        "common",
        ArgKind.INT,
        "Tokens to keep from the initial prompt. -1 = all.",
        default="0",
    ),
    ServerArg(
        "--swa-full",
        "common",
        ArgKind.BOOL,
        "Use full-size SWA cache.",
        default="false",
        env="LLAMA_ARG_SWA_FULL",
    ),
    ServerArg(
        "--flash-attn",
        "common",
        ArgKind.CHOICE,
        "Flash Attention use.",
        aliases=("-fa",),
        choices=_choice("on", "off", "auto"),
        default="auto",
        env="LLAMA_ARG_FLASH_ATTN",
    ),
    ServerArg(
        "--perf",
        "common",
        ArgKind.BOOL,
        "Enable internal libllama performance timings.",
        negated="--no-perf",
        default="false",
        env="LLAMA_ARG_PERF",
    ),
    ServerArg(
        "--escape",
        "common",
        ArgKind.BOOL,
        "Process escape sequences (\\n, \\r, \\t, ...).",
        aliases=("-e",),
        negated="--no-escape",
        default="true",
    ),
    ServerArg(
        "--rope-scaling",
        "common",
        ArgKind.CHOICE,
        "RoPE frequency scaling method; linear unless the model specifies one.",
        choices=_choice("none", "linear", "yarn"),
        env="LLAMA_ARG_ROPE_SCALING_TYPE",
    ),
    ServerArg(
        "--rope-scale",
        "common",
        ArgKind.FLOAT,
        "RoPE context scaling factor (expands context by N).",
        env="LLAMA_ARG_ROPE_SCALE",
    ),
    ServerArg(
        "--rope-freq-base",
        "common",
        ArgKind.FLOAT,
        "RoPE base frequency (NTK-aware scaling).",
        default="model",
        env="LLAMA_ARG_ROPE_FREQ_BASE",
    ),
    ServerArg(
        "--rope-freq-scale",
        "common",
        ArgKind.FLOAT,
        "RoPE frequency scaling factor (expands by 1/N).",
        env="LLAMA_ARG_ROPE_FREQ_SCALE",
    ),
    ServerArg(
        "--yarn-orig-ctx",
        "common",
        ArgKind.INT,
        "YaRN: original context size of the model.",
        default="0",
        env="LLAMA_ARG_YARN_ORIG_CTX",
    ),
    ServerArg(
        "--yarn-ext-factor",
        "common",
        ArgKind.FLOAT,
        "YaRN: extrapolation mix factor (0.0 = full interpolation).",
        default="-1.00",
        env="LLAMA_ARG_YARN_EXT_FACTOR",
    ),
    ServerArg(
        "--yarn-attn-factor",
        "common",
        ArgKind.FLOAT,
        "YaRN: scale sqrt(t) / attention magnitude.",
        default="-1.00",
        env="LLAMA_ARG_YARN_ATTN_FACTOR",
    ),
    ServerArg(
        "--yarn-beta-slow",
        "common",
        ArgKind.FLOAT,
        "YaRN: high correction dim / alpha.",
        default="-1.00",
        env="LLAMA_ARG_YARN_BETA_SLOW",
    ),
    ServerArg(
        "--yarn-beta-fast",
        "common",
        ArgKind.FLOAT,
        "YaRN: low correction dim / beta.",
        default="-1.00",
        env="LLAMA_ARG_YARN_BETA_FAST",
    ),
    ServerArg(
        "--kv-offload",
        "common",
        ArgKind.BOOL,
        "KV cache offloading.",
        aliases=("-kvo", "-nkvo"),
        negated="--no-kv-offload",
        default="true",
        env="LLAMA_ARG_KV_OFFLOAD",
    ),
    ServerArg(
        "--repack",
        "common",
        ArgKind.BOOL,
        "Enable weight repacking.",
        aliases=("-nr",),
        negated="--no-repack",
        default="true",
        env="LLAMA_ARG_REPACK",
    ),
    ServerArg(
        "--no-host",
        "common",
        ArgKind.BOOL,
        "Bypass host buffer, allowing extra buffers to be used.",
        default="false",
        env="LLAMA_ARG_NO_HOST",
    ),
    ServerArg(
        "--cache-type-k",
        "common",
        ArgKind.CHOICE,
        "KV cache data type for K.",
        aliases=("-ctk",),
        choices=_KV_TYPES,
        default="f16",
        env="LLAMA_ARG_CACHE_TYPE_K",
    ),
    ServerArg(
        "--cache-type-v",
        "common",
        ArgKind.CHOICE,
        "KV cache data type for V.",
        aliases=("-ctv",),
        choices=_KV_TYPES,
        default="f16",
        env="LLAMA_ARG_CACHE_TYPE_V",
    ),
    ServerArg(
        "--defrag-thold",
        "common",
        ArgKind.INT,
        "KV cache defragmentation threshold.",
        aliases=("-dt",),
        deprecated=True,
        env="LLAMA_ARG_DEFRAG_THOLD",
    ),
    ServerArg(
        "--rpc",
        "common",
        ArgKind.STRING,
        "Comma-separated list of RPC servers (host:port).",
        env="LLAMA_ARG_RPC",
    ),
    ServerArg(
        "--mlock",
        "common",
        ArgKind.BOOL,
        "Keep model in RAM (deprecated, use --load-mode).",
        default="false",
        deprecated=True,
        env="LLAMA_ARG_MLOCK",
    ),
    ServerArg(
        "--mmap",
        "common",
        ArgKind.BOOL,
        "Memory-map model (deprecated, use --load-mode).",
        negated="--no-mmap",
        default="true",
        deprecated=True,
        env="LLAMA_ARG_MMAP",
    ),
    ServerArg(
        "--direct-io",
        "common",
        ArgKind.BOOL,
        "Use DirectIO if available (deprecated, use --load-mode).",
        aliases=("-dio", "-ndio"),
        negated="--no-direct-io",
        deprecated=True,
        env="LLAMA_ARG_DIO",
    ),
    ServerArg(
        "--load-mode",
        "common",
        ArgKind.CHOICE,
        "Model loading mode.",
        aliases=("-lm",),
        choices=_choice("auto", "none", "mmap", "mlock", "mmap+mlock", "dio"),
        default="auto",
        env="LLAMA_ARG_LOAD_MODE",
    ),
    ServerArg(
        "--numa",
        "common",
        ArgKind.CHOICE,
        "NUMA optimisation strategy.",
        choices=_choice("distribute", "isolate", "numactl"),
        env="LLAMA_ARG_NUMA",
    ),
    ServerArg(
        "--device",
        "common",
        ArgKind.STRING,
        "Comma-separated devices for offloading (none = don't offload).",
        aliases=("-dev",),
        env="LLAMA_ARG_DEVICE",
    ),
    ServerArg(
        "--list-devices",
        "common",
        ArgKind.BOOL,
        "Print available devices and exit.",
        volatile=True,
    ),
    ServerArg(
        "--override-tensor",
        "common",
        ArgKind.STRING,
        "Override tensor buffer types: pattern=type,...",
        aliases=("-ot",),
        env="LLAMA_ARG_OVERRIDE_TENSOR",
    ),
    ServerArg(
        "--cpu-moe",
        "common",
        ArgKind.BOOL,
        "Keep all MoE weights in the CPU.",
        aliases=("-cmoe",),
        env="LLAMA_ARG_CPU_MOE",
    ),
    ServerArg(
        "--n-cpu-moe",
        "common",
        ArgKind.INT,
        "Keep MoE weights of the first N layers in the CPU.",
        aliases=("-ncmoe",),
        env="LLAMA_ARG_N_CPU_MOE",
    ),
    ServerArg(
        "--n-gpu-layers",
        "common",
        ArgKind.STRING,
        "Layers to store in VRAM: number, 'auto', or 'all'.",
        aliases=("-ngl", "--gpu-layers"),
        default="auto",
        env="LLAMA_ARG_N_GPU_LAYERS",
    ),
    ServerArg(
        "--split-mode",
        "common",
        ArgKind.CHOICE,
        "How to split the model across GPUs.",
        aliases=("-sm",),
        choices=_choice("none", "layer", "row", "tensor"),
        default="layer",
        env="LLAMA_ARG_SPLIT_MODE",
    ),
    ServerArg(
        "--tensor-split",
        "common",
        ArgKind.STRING,
        "Fraction per GPU, comma-separated, e.g. 3,1.",
        aliases=("-ts",),
        env="LLAMA_ARG_TENSOR_SPLIT",
    ),
    ServerArg(
        "--main-gpu",
        "common",
        ArgKind.INT,
        "GPU for the model (split none) / intermediate results and KV (split row).",
        aliases=("-mg",),
        default="0",
        env="LLAMA_ARG_MAIN_GPU",
    ),
    ServerArg(
        "--fit",
        "common",
        ArgKind.CHOICE,
        "Adjust unset arguments to fit in device memory.",
        aliases=("-fit",),
        choices=_choice("on", "off"),
        default="on",
        env="LLAMA_ARG_FIT",
    ),
    ServerArg(
        "--fit-target",
        "common",
        ArgKind.STRING,
        "Target margin per device for --fit, MiB, comma-separated.",
        aliases=("-fitt",),
        default="1024",
        env="LLAMA_ARG_FIT_TARGET",
    ),
    ServerArg(
        "--fit-ctx",
        "common",
        ArgKind.INT,
        "Minimum ctx size that --fit may set.",
        aliases=("-fitc",),
        default="4096",
        env="LLAMA_ARG_FIT_CTX",
    ),
    ServerArg(
        "--check-tensors",
        "common",
        ArgKind.BOOL,
        "Check model tensor data for invalid values.",
        default="false",
    ),
    ServerArg(
        "--override-kv",
        "common",
        ArgKind.STRING,
        "Override model metadata: KEY=TYPE:VALUE,...",
    ),
    ServerArg(
        "--op-offload",
        "common",
        ArgKind.BOOL,
        "Offload host tensor operations to device.",
        negated="--no-op-offload",
        default="true",
    ),
    ServerArg(
        "--lora",
        "common",
        ArgKind.PATH,
        "Path to LoRA adapter (comma-separated for multiple).",
    ),
    ServerArg(
        "--lora-scaled",
        "common",
        ArgKind.STRING,
        "LoRA adapter with scale: FNAME:SCALE,...",
    ),
    ServerArg(
        "--control-vector",
        "common",
        ArgKind.PATH,
        "Add a control vector (comma-separated for multiple).",
    ),
    ServerArg(
        "--control-vector-scaled",
        "common",
        ArgKind.STRING,
        "Control vector with scale: FNAME:SCALE,...",
    ),
    ServerArg(
        "--control-vector-layer-range",
        "common",
        ArgKind.STRING,
        "Layer range for control vectors: START END (inclusive).",
    ),
    ServerArg(
        "--model",
        "common",
        ArgKind.PATH,
        "Model path to load (the app always supplies the active model).",
        aliases=("-m",),
        env="LLAMA_ARG_MODEL",
        app_managed=True,
    ),
    ServerArg(
        "--model-url",
        "common",
        ArgKind.STRING,
        "Model download URL (overrides the local model).",
        aliases=("-mu",),
        env="LLAMA_ARG_MODEL_URL",
    ),
    ServerArg(
        "--docker-repo",
        "common",
        ArgKind.STRING,
        "Docker Hub model repository: [<repo>/]<model>[:quant].",
        aliases=("-dr",),
        env="LLAMA_ARG_DOCKER_REPO",
    ),
    ServerArg(
        "--hf-repo",
        "common",
        ArgKind.STRING,
        "Hugging Face repo: <user>/<model>[:quant]; mmproj auto-downloaded.",
        aliases=("-hf", "-hfr"),
        env="LLAMA_ARG_HF_REPO",
    ),
    ServerArg(
        "--hf-file",
        "common",
        ArgKind.STRING,
        "Hugging Face model file (overrides --hf-repo quant).",
        aliases=("-hff",),
        env="LLAMA_ARG_HF_FILE",
    ),
    ServerArg(
        "--hf-token",
        "common",
        ArgKind.STRING,
        "Hugging Face access token.",
        aliases=("-hft",),
        env="HF_TOKEN",
    ),
    ServerArg(
        "--log-disable", "common", ArgKind.BOOL, "Disable logging.", default="false"
    ),
    ServerArg(
        "--log-file", "common", ArgKind.PATH, "Log to file.", env="LLAMA_ARG_LOG_FILE"
    ),
    ServerArg(
        "--log-colors",
        "common",
        ArgKind.CHOICE,
        "Colored logging.",
        choices=_choice("on", "off", "auto"),
        default="auto",
        env="LLAMA_ARG_LOG_COLORS",
    ),
    ServerArg(
        "--verbose",
        "common",
        ArgKind.BOOL,
        "Log everything (verbosity = infinity).",
        aliases=("-v", "--log-verbose"),
        default="false",
    ),
    ServerArg(
        "--offline",
        "common",
        ArgKind.BOOL,
        "Offline mode: use cache, no network access.",
        default="false",
        env="LLAMA_ARG_OFFLINE",
    ),
    ServerArg(
        "--log-verbosity",
        "common",
        ArgKind.CHOICE,
        "Verbosity threshold: 0 generic..5 debug.",
        aliases=("-lv", "--verbosity"),
        choices=_choice("0", "1", "2", "3", "4", "5"),
        default="3",
        env="LLAMA_ARG_LOG_VERBOSITY",
    ),
    ServerArg(
        "--log-prefix",
        "common",
        ArgKind.BOOL,
        "Prefix in log messages.",
        negated="--no-log-prefix",
        env="LLAMA_ARG_LOG_PREFIX",
    ),
    ServerArg(
        "--log-timestamps",
        "common",
        ArgKind.BOOL,
        "Timestamps in log messages.",
        negated="--no-log-timestamps",
        default="true",
        env="LLAMA_ARG_LOG_TIMESTAMPS",
    ),
    ServerArg(
        "--spec-draft-type-k",
        "common",
        ArgKind.CHOICE,
        "KV cache data type for K, draft model.",
        aliases=("-ctkd", "--cache-type-k-draft"),
        choices=_KV_TYPES,
        default="f16",
        env="LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_K",
    ),
    ServerArg(
        "--spec-draft-type-v",
        "common",
        ArgKind.CHOICE,
        "KV cache data type for V, draft model.",
        aliases=("-ctvd", "--cache-type-v-draft"),
        choices=_KV_TYPES,
        default="f16",
        env="LLAMA_ARG_SPEC_DRAFT_CACHE_TYPE_V",
    ),
    # ─── sampling params ────────────────────────────────────────────────
    ServerArg(
        "--samplers",
        "sampling",
        ArgKind.STRING,
        "Samplers used for generation, ';'-separated.",
        default="penalties;dry;top_n_sigma;top_k;typ_p;top_p;min_p;xtc;temperature",
    ),
    ServerArg(
        "--seed",
        "sampling",
        ArgKind.INT,
        "RNG seed. -1 = random.",
        aliases=("-s",),
        default="-1",
    ),
    ServerArg(
        "--sampling-seq",
        "sampling",
        ArgKind.STRING,
        "Simplified sampler sequence.",
        aliases=("--sampler-seq",),
        default="edskypmxt",
    ),
    ServerArg(
        "--ignore-eos",
        "sampling",
        ArgKind.BOOL,
        "Ignore EOS token and continue (implies --logit-bias EOS-inf).",
        default="false",
    ),
    ServerArg(
        "--temperature",
        "sampling",
        ArgKind.FLOAT,
        "Temperature.",
        aliases=("--temp",),
        default="0.80",
    ),
    ServerArg(
        "--top-k",
        "sampling",
        ArgKind.INT,
        "Top-k sampling. 0 = disabled.",
        default="40",
        env="LLAMA_ARG_TOP_K",
    ),
    ServerArg(
        "--top-p",
        "sampling",
        ArgKind.FLOAT,
        "Top-p sampling. 1.0 = disabled.",
        default="0.95",
    ),
    ServerArg(
        "--min-p",
        "sampling",
        ArgKind.FLOAT,
        "Min-p sampling. 0.0 = disabled.",
        default="0.05",
    ),
    ServerArg(
        "--top-n-sigma",
        "sampling",
        ArgKind.FLOAT,
        "Top-n-sigma sampling. -1.0 = disabled.",
        aliases=("--top-nsigma",),
        default="-1.00",
    ),
    ServerArg(
        "--xtc-probability",
        "sampling",
        ArgKind.FLOAT,
        "XTC probability. 0.0 = disabled.",
        default="0.00",
    ),
    ServerArg(
        "--xtc-threshold",
        "sampling",
        ArgKind.FLOAT,
        "XTC threshold. 1.0 = disabled.",
        default="0.10",
    ),
    ServerArg(
        "--typical-p",
        "sampling",
        ArgKind.FLOAT,
        "Locally typical sampling, parameter p. 1.0 = disabled.",
        aliases=("--typical",),
        default="1.00",
    ),
    ServerArg(
        "--repeat-last-n",
        "sampling",
        ArgKind.INT,
        "Last n tokens to consider for penalization. 0 = disabled.",
        default="64",
    ),
    ServerArg(
        "--repeat-penalty",
        "sampling",
        ArgKind.FLOAT,
        "Penalize repeated sequences. 1.0 = disabled.",
        default="1.00",
    ),
    ServerArg(
        "--presence-penalty",
        "sampling",
        ArgKind.FLOAT,
        "Presence penalty. 0.0 = disabled.",
        default="0.00",
    ),
    ServerArg(
        "--frequency-penalty",
        "sampling",
        ArgKind.FLOAT,
        "Frequency penalty. 0.0 = disabled.",
        default="0.00",
    ),
    ServerArg(
        "--dry-multiplier",
        "sampling",
        ArgKind.FLOAT,
        "DRY sampling multiplier. 0.0 = disabled.",
        default="0.00",
    ),
    ServerArg(
        "--dry-base",
        "sampling",
        ArgKind.FLOAT,
        "DRY sampling base value.",
        default="1.75",
    ),
    ServerArg(
        "--dry-allowed-length",
        "sampling",
        ArgKind.INT,
        "Allowed length for DRY sampling.",
        default="2",
    ),
    ServerArg(
        "--dry-penalty-last-n",
        "sampling",
        ArgKind.INT,
        "DRY penalty for the last n tokens. 0 = disabled.",
        default="64",
    ),
    ServerArg(
        "--dry-sequence-breaker",
        "sampling",
        ArgKind.STRING,
        "Add a DRY sequence breaker (clears defaults; 'none' = no breakers).",
    ),
    ServerArg(
        "--adaptive-target",
        "sampling",
        ArgKind.FLOAT,
        "Adaptive-p: select tokens near this probability (negative = disabled).",
        default="-1.00",
    ),
    ServerArg(
        "--adaptive-decay",
        "sampling",
        ArgKind.FLOAT,
        "Adaptive-p: decay rate for target adaptation (0.0-0.99).",
        default="0.90",
    ),
    ServerArg(
        "--dynatemp-range",
        "sampling",
        ArgKind.FLOAT,
        "Dynamic temperature range. 0.0 = disabled.",
        default="0.00",
    ),
    ServerArg(
        "--dynatemp-exp",
        "sampling",
        ArgKind.FLOAT,
        "Dynamic temperature exponent.",
        default="1.00",
    ),
    ServerArg(
        "--mirostat",
        "sampling",
        ArgKind.CHOICE,
        "Mirostat sampling: 0 disabled, 1 = Mirostat, 2 = Mirostat 2.0.",
        choices=_choice("0", "1", "2"),
        default="0",
    ),
    ServerArg(
        "--mirostat-lr",
        "sampling",
        ArgKind.FLOAT,
        "Mirostat learning rate, parameter eta.",
        default="0.10",
    ),
    ServerArg(
        "--mirostat-ent",
        "sampling",
        ArgKind.FLOAT,
        "Mirostat target entropy, parameter tau.",
        default="5.00",
    ),
    ServerArg(
        "--logit-bias",
        "sampling",
        ArgKind.STRING,
        "Token logit bias: TOKEN_ID(+/-)BIAS (repeatable).",
        aliases=("-l",),
    ),
    ServerArg(
        "--grammar",
        "sampling",
        ArgKind.STRING,
        "BNF-like grammar to constrain generations.",
    ),
    ServerArg(
        "--grammar-file", "sampling", ArgKind.PATH, "File to read the grammar from."
    ),
    ServerArg(
        "--json-schema",
        "sampling",
        ArgKind.STRING,
        "JSON schema to constrain generations.",
        aliases=("-j",),
        default="{}",
    ),
    ServerArg(
        "--json-schema-file",
        "sampling",
        ArgKind.PATH,
        "File containing a JSON schema.",
        aliases=("-jf",),
    ),
    ServerArg(
        "--backend-sampling",
        "sampling",
        ArgKind.BOOL,
        "Enable backend sampling (experimental).",
        aliases=("-bs",),
        default="false",
        env="LLAMA_ARG_BACKEND_SAMPLING",
    ),
    # ─── speculative params ─────────────────────────────────────────────
    ServerArg(
        "--hf-repo-draft",
        "speculative",
        ArgKind.STRING,
        "HF repo for the draft model.",
        aliases=("-hfd", "-hfrd", "--spec-draft-hf"),
        env="LLAMA_ARG_SPEC_DRAFT_HF_REPO",
    ),
    ServerArg(
        "--threads-draft",
        "speculative",
        ArgKind.INT,
        "CPU threads for the draft model.",
        aliases=("-td", "--spec-draft-threads"),
        default="same as --threads",
    ),
    ServerArg(
        "--threads-batch-draft",
        "speculative",
        ArgKind.INT,
        "CPU threads for draft batch / prompt processing.",
        aliases=("-tbd", "--spec-draft-threads-batch"),
        default="same as --threads-draft",
    ),
    ServerArg(
        "--cpu-mask-draft",
        "speculative",
        ArgKind.STRING,
        "Draft model CPU affinity mask.",
        aliases=("-Cd", "--spec-draft-cpu-mask"),
        default="same as --cpu-mask",
    ),
    ServerArg(
        "--cpu-range-draft",
        "speculative",
        ArgKind.STRING,
        "Draft model CPU affinity range.",
        aliases=("-Crd", "--spec-draft-cpu-range"),
    ),
    ServerArg(
        "--cpu-strict-draft",
        "speculative",
        ArgKind.CHOICE,
        "Strict CPU placement for the draft model.",
        aliases=("--spec-draft-cpu-strict",),
        choices=_choice("0", "1"),
        default="same as --cpu-strict",
    ),
    ServerArg(
        "--prio-draft",
        "speculative",
        ArgKind.INT,
        "Draft process/thread priority: 0-normal..3-realtime.",
        aliases=("--spec-draft-prio",),
        default="0",
    ),
    ServerArg(
        "--poll-draft",
        "speculative",
        ArgKind.CHOICE,
        "Polling for draft model work.",
        aliases=("--spec-draft-poll",),
        choices=_choice("0", "1"),
        default="same as --poll",
    ),
    ServerArg(
        "--cpu-mask-batch-draft",
        "speculative",
        ArgKind.STRING,
        "Draft batch CPU affinity mask.",
        aliases=("-Cbd", "--spec-draft-cpu-mask-batch"),
        default="same as --cpu-mask",
    ),
    ServerArg(
        "--cpu-strict-batch-draft",
        "speculative",
        ArgKind.CHOICE,
        "Strict CPU placement for draft batch.",
        aliases=("--spec-draft-cpu-strict-batch",),
        choices=_choice("0", "1"),
        default="same as --cpu-strict-draft",
    ),
    ServerArg(
        "--prio-batch-draft",
        "speculative",
        ArgKind.INT,
        "Draft batch priority: 0-normal..3-realtime.",
        aliases=("--spec-draft-prio-batch",),
        default="0",
    ),
    ServerArg(
        "--poll-batch-draft",
        "speculative",
        ArgKind.CHOICE,
        "Polling for draft batch work.",
        aliases=("--spec-draft-poll-batch",),
        choices=_choice("0", "1"),
        default="same as --poll-draft",
    ),
    ServerArg(
        "--override-tensor-draft",
        "speculative",
        ArgKind.STRING,
        "Override tensor buffer types for the draft model.",
        aliases=("-otd", "--spec-draft-override-tensor"),
    ),
    ServerArg(
        "--cpu-moe-draft",
        "speculative",
        ArgKind.BOOL,
        "Keep all draft MoE weights in the CPU.",
        aliases=("-cmoed", "--spec-draft-cpu-moe"),
        env="LLAMA_ARG_SPEC_DRAFT_CPU_MOE",
    ),
    ServerArg(
        "--n-cpu-moe-draft",
        "speculative",
        ArgKind.INT,
        "Keep draft MoE weights of the first N layers in the CPU.",
        aliases=("-ncmoed", "--spec-draft-ncmoe", "--spec-draft-n-cpu-moe"),
        env="LLAMA_ARG_SPEC_DRAFT_N_CPU_MOE",
    ),
    ServerArg(
        "--spec-draft-n-max",
        "speculative",
        ArgKind.INT,
        "Number of tokens to draft for speculative decoding.",
        default="3",
        env="LLAMA_ARG_SPEC_DRAFT_N_MAX",
    ),
    ServerArg(
        "--spec-draft-n-min",
        "speculative",
        ArgKind.INT,
        "Minimum draft tokens to use.",
        default="0",
        env="LLAMA_ARG_SPEC_DRAFT_N_MIN",
    ),
    ServerArg(
        "--spec-draft-p-split",
        "speculative",
        ArgKind.FLOAT,
        "Speculative decoding split probability.",
        aliases=("--draft-p-split",),
        default="0.10",
        env="LLAMA_ARG_SPEC_DRAFT_P_SPLIT",
    ),
    ServerArg(
        "--spec-draft-p-min",
        "speculative",
        ArgKind.FLOAT,
        "Minimum speculative decoding probability.",
        aliases=("--draft-p-min",),
        default="0.00",
        env="LLAMA_ARG_SPEC_DRAFT_P_MIN",
    ),
    ServerArg(
        "--spec-draft-backend-sampling",
        "speculative",
        ArgKind.BOOL,
        "Offload draft sampling to the backend.",
        negated="--no-spec-draft-backend-sampling",
        default="true",
        env="LLAMA_ARG_SPEC_DRAFT_BACKEND_SAMPLING",
    ),
    ServerArg(
        "--device-draft",
        "speculative",
        ArgKind.STRING,
        "Devices for offloading the draft model.",
        aliases=("-devd", "--spec-draft-device"),
        env="LLAMA_ARG_SPEC_DRAFT_DEVICE",
    ),
    ServerArg(
        "--n-gpu-layers-draft",
        "speculative",
        ArgKind.STRING,
        "Draft model layers in VRAM: number, 'auto', or 'all'.",
        aliases=("-ngld", "--gpu-layers-draft", "--spec-draft-ngl"),
        default="auto",
        env="LLAMA_ARG_N_GPU_LAYERS_DRAFT",
    ),
    ServerArg(
        "--model-draft",
        "speculative",
        ArgKind.PATH,
        "Draft model for speculative decoding.",
        aliases=("-md", "--spec-draft-model"),
        env="LLAMA_ARG_SPEC_DRAFT_MODEL",
    ),
    ServerArg(
        "--spec-type",
        "speculative",
        ArgKind.STRING,
        "Comma-separated speculative-decoding types.",
        default="none",
        env="LLAMA_ARG_SPEC_TYPE",
    ),
    ServerArg(
        "--spec-ngram-mod-n-min",
        "speculative",
        ArgKind.INT,
        "Minimum ngram tokens for ngram-mod.",
        default="48",
    ),
    ServerArg(
        "--spec-ngram-mod-n-max",
        "speculative",
        ArgKind.INT,
        "Maximum ngram tokens for ngram-mod.",
        default="64",
    ),
    ServerArg(
        "--spec-ngram-mod-n-match",
        "speculative",
        ArgKind.INT,
        "ngram-mod lookup length.",
        default="24",
    ),
    ServerArg(
        "--spec-ngram-simple-size-n",
        "speculative",
        ArgKind.INT,
        "ngram size N for ngram-simple.",
        default="12",
    ),
    ServerArg(
        "--spec-ngram-simple-size-m",
        "speculative",
        ArgKind.INT,
        "ngram size M for ngram-simple.",
        default="48",
    ),
    ServerArg(
        "--spec-ngram-simple-min-hits",
        "speculative",
        ArgKind.INT,
        "Minimum hits for ngram-simple.",
        default="1",
    ),
    ServerArg(
        "--spec-ngram-map-k-size-n",
        "speculative",
        ArgKind.INT,
        "ngram size N for ngram-map-k.",
        default="12",
    ),
    ServerArg(
        "--spec-ngram-map-k-size-m",
        "speculative",
        ArgKind.INT,
        "ngram size M for ngram-map-k.",
        default="48",
    ),
    ServerArg(
        "--spec-ngram-map-k-min-hits",
        "speculative",
        ArgKind.INT,
        "Minimum hits for ngram-map-k.",
        default="1",
    ),
    ServerArg(
        "--spec-ngram-map-k4v-size-n",
        "speculative",
        ArgKind.INT,
        "ngram size N for ngram-map-k4v.",
        default="12",
    ),
    ServerArg(
        "--spec-ngram-map-k4v-size-m",
        "speculative",
        ArgKind.INT,
        "ngram size M for ngram-map-k4v.",
        default="48",
    ),
    ServerArg(
        "--spec-ngram-map-k4v-min-hits",
        "speculative",
        ArgKind.INT,
        "Minimum hits for ngram-map-k4v.",
        default="1",
    ),
    ServerArg(
        "--draft-n-max",
        "speculative",
        ArgKind.INT,
        "REMOVED: use --spec-draft-n-max or --spec-ngram-mod-n-max.",
        aliases=("--draft", "--draft-n", "--draft-max"),
        deprecated=True,
    ),
    ServerArg(
        "--draft-n-min",
        "speculative",
        ArgKind.INT,
        "REMOVED: use --spec-draft-n-min or --spec-ngram-mod-n-min.",
        aliases=("--draft-min",),
        deprecated=True,
    ),
    ServerArg(
        "--spec-ngram-size-n",
        "speculative",
        ArgKind.INT,
        "REMOVED: use the respective --spec-ngram-*-size-n.",
        deprecated=True,
    ),
    ServerArg(
        "--spec-ngram-size-m",
        "speculative",
        ArgKind.INT,
        "REMOVED: use the respective --spec-ngram-*-size-m.",
        deprecated=True,
    ),
    ServerArg(
        "--spec-ngram-min-hits",
        "speculative",
        ArgKind.INT,
        "REMOVED: use the respective --spec-ngram-*-min-hits.",
        deprecated=True,
    ),
    # ─── example-specific (server) params ───────────────────────────────
    ServerArg(
        "--lookup-cache-static",
        "server",
        ArgKind.PATH,
        "Static lookup cache for lookup decoding.",
        aliases=("-lcs",),
    ),
    ServerArg(
        "--lookup-cache-dynamic",
        "server",
        ArgKind.PATH,
        "Dynamic lookup cache (updated by generation).",
        aliases=("-lcd",),
    ),
    ServerArg(
        "--ctx-checkpoints",
        "server",
        ArgKind.INT,
        "Max context checkpoints per slot.",
        aliases=("-ctxcp", "--swa-checkpoints"),
        default="32",
        env="LLAMA_ARG_CTX_CHECKPOINTS",
    ),
    ServerArg(
        "--checkpoint-min-step",
        "server",
        ArgKind.INT,
        "Minimum spacing between context checkpoints in tokens. 0 = no minimum.",
        aliases=("-cms",),
        default="8192",
        env="LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT",
    ),
    ServerArg(
        "--cache-ram",
        "server",
        ArgKind.INT,
        "Maximum cache size in MiB. -1 = no limit, 0 = disable.",
        aliases=("-cram",),
        default="8192",
        env="LLAMA_ARG_CACHE_RAM",
    ),
    ServerArg(
        "--kv-unified",
        "server",
        ArgKind.BOOL,
        "Single unified KV buffer shared across all sequences.",
        aliases=("-kvu", "-no-kvu"),
        negated="--no-kv-unified",
        env="LLAMA_ARG_KV_UNIFIED",
    ),
    ServerArg(
        "--cache-idle-slots",
        "server",
        ArgKind.BOOL,
        "Save idle slots to the prompt cache on a new task (requires cache-ram).",
        negated="--no-cache-idle-slots",
        env="LLAMA_ARG_CACHE_IDLE_SLOTS",
    ),
    ServerArg(
        "--context-shift",
        "server",
        ArgKind.BOOL,
        "Use context shift on infinite text generation.",
        negated="--no-context-shift",
        env="LLAMA_ARG_CONTEXT_SHIFT",
    ),
    ServerArg(
        "--reverse-prompt",
        "server",
        ArgKind.STRING,
        "Halt generation at PROMPT (return control).",
        aliases=("-r",),
    ),
    ServerArg(
        "--special",
        "server",
        ArgKind.BOOL,
        "Output special tokens.",
        aliases=("-sp",),
        default="false",
    ),
    ServerArg(
        "--warmup",
        "server",
        ArgKind.BOOL,
        "Perform warmup with an empty run.",
        negated="--no-warmup",
        default="true",
    ),
    ServerArg(
        "--spm-infill",
        "server",
        ArgKind.BOOL,
        "Use Suffix/Prefix/Middle pattern for infill.",
        default="false",
    ),
    ServerArg(
        "--pooling",
        "server",
        ArgKind.CHOICE,
        "Pooling type for embeddings.",
        choices=_choice("none", "mean", "cls", "last", "rank"),
        env="LLAMA_ARG_POOLING",
    ),
    ServerArg(
        "--parallel",
        "server",
        ArgKind.INT,
        "Number of server slots. -1 = auto.",
        aliases=("-np",),
        default="-1",
        env="LLAMA_ARG_N_PARALLEL",
    ),
    ServerArg(
        "--cont-batching",
        "server",
        ArgKind.BOOL,
        "Enable continuous (dynamic) batching.",
        aliases=("-cb", "-nocb"),
        negated="--no-cont-batching",
        env="LLAMA_ARG_CONT_BATCHING",
    ),
    ServerArg(
        "--mmproj",
        "server",
        ArgKind.PATH,
        "Path to a multimodal projector file.",
        aliases=("-mm",),
        env="LLAMA_ARG_MMPROJ",
    ),
    ServerArg(
        "--mmproj-url",
        "server",
        ArgKind.STRING,
        "URL to a multimodal projector file.",
        aliases=("-mmu",),
        env="LLAMA_ARG_MMPROJ_URL",
    ),
    ServerArg(
        "--mmproj-auto",
        "server",
        ArgKind.BOOL,
        "Use a multimodal projector if available (useful with -hf).",
        aliases=("--no-mmproj", "--no-mmproj-auto"),
        negated="--no-mmproj-auto",
        env="LLAMA_ARG_MMPROJ_AUTO",
    ),
    ServerArg(
        "--mmproj-offload",
        "server",
        ArgKind.BOOL,
        "Enable GPU offloading for the multimodal projector.",
        negated="--no-mmproj-offload",
        env="LLAMA_ARG_MMPROJ_OFFLOAD",
    ),
    ServerArg(
        "--image-min-tokens",
        "server",
        ArgKind.INT,
        "Minimum tokens each image can take (vision).",
        env="LLAMA_ARG_IMAGE_MIN_TOKENS",
    ),
    ServerArg(
        "--image-max-tokens",
        "server",
        ArgKind.INT,
        "Maximum tokens each image can take (vision).",
        env="LLAMA_ARG_IMAGE_MAX_TOKENS",
    ),
    ServerArg(
        "--mtmd-batch-max-tokens",
        "server",
        ArgKind.INT,
        "Max image tokens per batch when encoding images.",
        default="1024",
        env="LLAMA_ARG_MTMD_BATCH_MAX_TOKENS",
    ),
    ServerArg(
        "--alias",
        "server",
        ArgKind.STRING,
        "Model name aliases, comma-separated (for the API).",
        aliases=("-a",),
        env="LLAMA_ARG_ALIAS",
    ),
    ServerArg(
        "--tags",
        "server",
        ArgKind.STRING,
        "Model tags, comma-separated (informational).",
        env="LLAMA_ARG_TAGS",
    ),
    ServerArg(
        "--embd-normalize",
        "server",
        ArgKind.INT,
        "Embedding normalisation: -1 none, 0 max-int16, 1 taxicab, 2 euclidean, >2 p-norm.",
        default="2",
    ),
    ServerArg(
        "--host",
        "server",
        ArgKind.STRING,
        "IP to listen on, or a .sock UNIX socket.",
        default="127.0.0.1",
        env="LLAMA_ARG_HOST",
    ),
    ServerArg(
        "--port",
        "server",
        ArgKind.INT,
        "Port to listen on.",
        default="8080",
        env="LLAMA_ARG_PORT",
    ),
    ServerArg(
        "--reuse-port",
        "server",
        ArgKind.BOOL,
        "Allow multiple sockets to bind the same port.",
        default="false",
        env="LLAMA_ARG_REUSE_PORT",
    ),
    ServerArg(
        "--path",
        "server",
        ArgKind.PATH,
        "Path to serve static files from.",
        is_dir=True,
        env="LLAMA_ARG_STATIC_PATH",
    ),
    ServerArg(
        "--cors-origins",
        "server",
        ArgKind.STRING,
        "Allowed origins for CORS ('localhost' reflects the Origin header).",
        default="*",
        env="LLAMA_ARG_CORS_ORIGINS",
    ),
    ServerArg(
        "--cors-methods",
        "server",
        ArgKind.STRING,
        "Allowed methods for CORS.",
        default="GET, POST, DELETE, OPTIONS",
        env="LLAMA_ARG_CORS_METHODS",
    ),
    ServerArg(
        "--cors-headers",
        "server",
        ArgKind.STRING,
        "Allowed headers for CORS.",
        default="*",
        env="LLAMA_ARG_CORS_HEADERS",
    ),
    ServerArg(
        "--cors-credentials",
        "server",
        ArgKind.BOOL,
        "Allow credentials for CORS.",
        negated="--no-cors-credentials",
        env="LLAMA_ARG_CORS_CREDENTIALS",
    ),
    ServerArg(
        "--api-prefix",
        "server",
        ArgKind.STRING,
        "Prefix path the server serves from (no trailing slash).",
        env="LLAMA_ARG_API_PREFIX",
    ),
    ServerArg(
        "--ui-config",
        "server",
        ArgKind.STRING,
        "JSON with default UI settings (overrides UI defaults).",
        aliases=("--webui-config",),
        env="LLAMA_ARG_UI_CONFIG",
    ),
    ServerArg(
        "--ui-config-file",
        "server",
        ArgKind.PATH,
        "JSON file with default UI settings.",
        aliases=("--webui-config-file",),
        env="LLAMA_ARG_UI_CONFIG_FILE",
    ),
    ServerArg(
        "--ui-mcp-proxy",
        "server",
        ArgKind.BOOL,
        "Experimental: MCP CORS proxy (untrusted environments only).",
        aliases=("--webui-mcp-proxy", "--no-ui-mcp-proxy", "--no-webui-mcp-proxy"),
        negated="--no-ui-mcp-proxy",
        env="LLAMA_ARG_UI_MCP_PROXY",
    ),
    ServerArg(
        "--tools",
        "server",
        ArgKind.STRING,
        "Experimental: enable built-in tools for AI agents (or 'all').",
        env="LLAMA_ARG_TOOLS",
    ),
    ServerArg(
        "--tools-runtime",
        "server",
        ArgKind.STRING,
        "Run tools in a separate runtime: docker/podman/ssh:...",
        env="LLAMA_ARG_TOOLS_RUNTIME",
    ),
    ServerArg(
        "--mcp-servers-config",
        "server",
        ArgKind.PATH,
        "JSON file with MCP server definitions (Cursor-compatible).",
        env="LLAMA_ARG_MCP_SERVERS_CONFIG",
    ),
    ServerArg(
        "--mcp-servers-json",
        "server",
        ArgKind.STRING,
        "Inline JSON with MCP server definitions.",
        env="LLAMA_ARG_MCP_SERVERS_JSON",
    ),
    ServerArg(
        "--agent",
        "server",
        ArgKind.BOOL,
        "Enable the CORS proxy and all built-in tools.",
        aliases=("-ag", "-no-ag"),
        negated="--no-agent",
        env="LLAMA_ARG_AGENT",
    ),
    ServerArg(
        "--ui",
        "server",
        ArgKind.BOOL,
        "Enable the Web UI.",
        aliases=("--webui", "--no-ui", "--no-webui"),
        negated="--no-ui",
        env="LLAMA_ARG_UI",
    ),
    ServerArg(
        "--embedding",
        "server",
        ArgKind.BOOL,
        "Restrict to the embedding use case.",
        aliases=("--embeddings",),
        env="LLAMA_ARG_EMBEDDINGS",
    ),
    ServerArg(
        "--rerank",
        "server",
        ArgKind.BOOL,
        "Enable the reranking endpoint.",
        aliases=("--reranking",),
        env="LLAMA_ARG_RERANKING",
    ),
    ServerArg(
        "--api-key",
        "server",
        ArgKind.STRING,
        "API key(s) for authentication, comma-separated.",
        env="LLAMA_API_KEY",
    ),
    ServerArg(
        "--api-key-file",
        "server",
        ArgKind.PATH,
        "File with API keys, one per line ('#' = comment).",
        env="LLAMA_ARG_API_KEY_FILE",
    ),
    ServerArg(
        "--ssl-key-file",
        "server",
        ArgKind.PATH,
        "PEM-encoded SSL private key.",
        env="LLAMA_ARG_SSL_KEY_FILE",
    ),
    ServerArg(
        "--ssl-cert-file",
        "server",
        ArgKind.PATH,
        "PEM-encoded SSL certificate.",
        env="LLAMA_ARG_SSL_CERT_FILE",
    ),
    ServerArg(
        "--chat-template-kwargs",
        "server",
        ArgKind.STRING,
        "Extra params for the JSON template parser (valid JSON).",
    ),
    ServerArg(
        "--timeout",
        "server",
        ArgKind.INT,
        "Server read/write timeout in seconds.",
        aliases=("-to",),
        default="3600",
        env="LLAMA_ARG_TIMEOUT",
    ),
    ServerArg(
        "--sse-ping-interval",
        "server",
        ArgKind.INT,
        "SSE ping interval in seconds. -1 = disabled.",
        default="30",
        env="LLAMA_ARG_SSE_PING_INTERVAL",
    ),
    ServerArg(
        "--threads-http",
        "server",
        ArgKind.INT,
        "Threads used to process HTTP requests. -1 = auto.",
        default="-1",
        env="LLAMA_ARG_THREADS_HTTP",
    ),
    ServerArg(
        "--cache-prompt",
        "server",
        ArgKind.BOOL,
        "Enable prompt caching.",
        negated="--no-cache-prompt",
        env="LLAMA_ARG_CACHE_PROMPT",
    ),
    ServerArg(
        "--cache-reuse",
        "server",
        ArgKind.INT,
        "Minimum chunk size to reuse from the cache via KV shifting.",
        default="0",
        env="LLAMA_ARG_CACHE_REUSE",
    ),
    ServerArg(
        "--metrics",
        "server",
        ArgKind.BOOL,
        "Enable the Prometheus metrics endpoint.",
        env="LLAMA_ARG_ENDPOINT_METRICS",
    ),
    ServerArg(
        "--props",
        "server",
        ArgKind.BOOL,
        "Enable changing global properties via POST /props.",
        env="LLAMA_ARG_ENDPOINT_PROPS",
    ),
    ServerArg(
        "--slots",
        "server",
        ArgKind.BOOL,
        "Expose the slots monitoring endpoint.",
        negated="--no-slots",
        env="LLAMA_ARG_ENDPOINT_SLOTS",
    ),
    ServerArg(
        "--slot-save-path",
        "server",
        ArgKind.PATH,
        "Path to save slot KV cache.",
        is_dir=True,
    ),
    ServerArg(
        "--media-path",
        "server",
        ArgKind.PATH,
        "Directory for loading local media files (file:// URLs).",
        is_dir=True,
    ),
    ServerArg(
        "--models-dir",
        "server",
        ArgKind.PATH,
        "Directory of models for the router server.",
        is_dir=True,
        env="LLAMA_ARG_MODELS_DIR",
    ),
    ServerArg(
        "--models-preset",
        "server",
        ArgKind.PATH,
        "INI file with model presets for the router server.",
        env="LLAMA_ARG_MODELS_PRESET",
    ),
    ServerArg(
        "--models-max",
        "server",
        ArgKind.INT,
        "Router server: max models loaded simultaneously. 0 = unlimited.",
        default="4",
        env="LLAMA_ARG_MODELS_MAX",
    ),
    ServerArg(
        "--models-autoload",
        "server",
        ArgKind.BOOL,
        "Router server: automatically load models.",
        negated="--no-models-autoload",
        env="LLAMA_ARG_MODELS_AUTOLOAD",
    ),
    ServerArg(
        "--jinja",
        "server",
        ArgKind.BOOL,
        "Use the Jinja template engine for chat.",
        negated="--no-jinja",
        env="LLAMA_ARG_JINJA",
    ),
    ServerArg(
        "--reasoning-format",
        "server",
        ArgKind.CHOICE,
        "Whether/how thought tags are extracted.",
        choices=_choice("none", "deepseek", "deepseek-legacy", "auto"),
        default="auto",
        env="LLAMA_ARG_THINK",
    ),
    ServerArg(
        "--reasoning",
        "server",
        ArgKind.CHOICE,
        "Use reasoning/thinking in chat.",
        aliases=("-rea",),
        choices=_choice("on", "off", "auto"),
        default="auto",
        env="LLAMA_ARG_REASONING",
    ),
    ServerArg(
        "--reasoning-effort",
        "server",
        ArgKind.CHOICE,
        "Reasoning effort level for the chat template.",
        choices=_choice("default", "minimal", "low", "medium", "high", "xhigh", "max"),
        default="default",
        env="LLAMA_ARG_REASONING_EFFORT",
    ),
    ServerArg(
        "--reasoning-budget",
        "server",
        ArgKind.INT,
        "Token budget for thinking. -1 unrestricted, 0 immediate end.",
        default="-1",
        env="LLAMA_ARG_THINK_BUDGET",
    ),
    ServerArg(
        "--reasoning-budget-message",
        "server",
        ArgKind.STRING,
        "Message injected before the end-of-thinking tag when the budget is exhausted.",
        env="LLAMA_ARG_THINK_BUDGET_MESSAGE",
    ),
    ServerArg(
        "--reasoning-preserve",
        "server",
        ArgKind.BOOL,
        "Preserve the reasoning trace in the full history.",
        negated="--no-reasoning-preserve",
        env="LLAMA_ARG_REASONING_PRESERVE",
    ),
    ServerArg(
        "--chat-template",
        "server",
        ArgKind.STRING,
        "Custom Jinja chat template (built-in names listed in --help).",
        env="LLAMA_ARG_CHAT_TEMPLATE",
    ),
    ServerArg(
        "--chat-template-file",
        "server",
        ArgKind.PATH,
        "File with a custom Jinja chat template.",
        env="LLAMA_ARG_CHAT_TEMPLATE_FILE",
    ),
    ServerArg(
        "--skip-chat-parsing",
        "server",
        ArgKind.BOOL,
        "Force a pure content parser (no tool/reasoning parsing).",
        negated="--no-skip-chat-parsing",
        env="LLAMA_ARG_SKIP_CHAT_PARSING",
    ),
    ServerArg(
        "--prefill-assistant",
        "server",
        ArgKind.BOOL,
        "Prefill the assistant's response when the last message is from the assistant.",
        negated="--no-prefill-assistant",
        env="LLAMA_ARG_PREFILL_ASSISTANT",
    ),
    ServerArg(
        "--slot-prompt-similarity",
        "server",
        ArgKind.FLOAT,
        "Prompt similarity required to reuse a slot. 0.0 = disabled.",
        aliases=("-sps",),
        default="0.10",
    ),
    ServerArg(
        "--lora-init-without-apply",
        "server",
        ArgKind.BOOL,
        "Load LoRA adapters without applying them (apply via /lora-adapters).",
    ),
    ServerArg(
        "--sleep-idle-seconds",
        "server",
        ArgKind.INT,
        "Seconds of idleness before the server sleeps. -1 = disabled.",
        default="-1",
    ),
    ServerArg(
        "--log-prompts-dir",
        "server",
        ArgKind.PATH,
        "Log prompts to a directory (debugging).",
        is_dir=True,
    ),
    ServerArg(
        "--embd-gemma-default",
        "server",
        ArgKind.BOOL,
        "Use the default EmbeddingGemma model (may download weights).",
    ),
    ServerArg(
        "--fim-qwen-1.5b-default",
        "server",
        ArgKind.BOOL,
        "Use the default Qwen 2.5 Coder 1.5B (may download weights).",
    ),
    ServerArg(
        "--fim-qwen-3b-default",
        "server",
        ArgKind.BOOL,
        "Use the default Qwen 2.5 Coder 3B (may download weights).",
    ),
    ServerArg(
        "--fim-qwen-7b-default",
        "server",
        ArgKind.BOOL,
        "Use the default Qwen 2.5 Coder 7B (may download weights).",
    ),
    ServerArg(
        "--fim-qwen-7b-spec",
        "server",
        ArgKind.BOOL,
        "Use Qwen 2.5 Coder 7B + 0.5B draft for speculative decoding (may download weights).",
    ),
    ServerArg(
        "--fim-qwen-14b-spec",
        "server",
        ArgKind.BOOL,
        "Use Qwen 2.5 Coder 14B + 0.5B draft for speculative decoding (may download weights).",
    ),
    ServerArg(
        "--fim-qwen-30b-default",
        "server",
        ArgKind.BOOL,
        "Use the default Qwen 3 Coder 30B A3B Instruct (may download weights).",
    ),
    ServerArg(
        "--gpt-oss-20b-default",
        "server",
        ArgKind.BOOL,
        "Use gpt-oss-20b (may download weights).",
    ),
    ServerArg(
        "--gpt-oss-120b-default",
        "server",
        ArgKind.BOOL,
        "Use gpt-oss-120b (may download weights).",
    ),
    ServerArg(
        "--vision-gemma-4b-default",
        "server",
        ArgKind.BOOL,
        "Use Gemma 3 4B QAT (may download weights).",
    ),
    ServerArg(
        "--vision-gemma-12b-default",
        "server",
        ArgKind.BOOL,
        "Use Gemma 3 12B QAT (may download weights).",
    ),
    ServerArg(
        "--spec-default",
        "server",
        ArgKind.BOOL,
        "Enable the default speculative decoding configuration.",
    ),
)
# ─── Lookup index ──────────────────────────────────────────────────────────

_SERVER_ARGS_BY_FLAG: dict[str, ServerArg] = {arg.flag: arg for arg in SERVER_ARGS}
_SERVER_ARGS_BY_ALIAS: dict[str, ServerArg] = {}
for _arg in SERVER_ARGS:
    for _alias in _arg.aliases:
        _SERVER_ARGS_BY_ALIAS.setdefault(_alias, _arg)


def options_to_cli(options: Mapping[str, str]) -> list[str]:
    """Serialize a ``{flag: value}`` map to CLI tokens (catalogue order).

    Dedicated flags (``--host``/``--port``/``--ctx-size``/``--n-gpu-layers``)
    are skipped — :func:`llamagui.lifecycle.build_llama_server_args` emits them
    from the dedicated config fields so there is a single source of truth.
    Blank values are ignored (the flag is omitted so the binary default wins).
    """
    tokens: list[str] = []
    for arg in SERVER_ARGS:
        if arg.flag in DEDICATED_FLAGS:
            continue
        value = options.get(arg.flag)
        if value is None or str(value).strip() == "":
            continue
        tokens.extend(_value_to_cli(arg, str(value)))
    return tokens


def _value_to_cli(arg: ServerArg, value: str) -> list[str]:
    value = value.strip()
    if arg.kind is ArgKind.BOOL:
        lowered = value.lower()
        if lowered in _TRUE:
            return [arg.flag]
        if lowered in _FALSE:
            return [arg.negated] if arg.negated else []
        raise ValueError(f"{arg.flag}: invalid boolean value '{value}' (use on/off)")
    return [arg.flag, value]


def find_arg(name: str) -> ServerArg | None:
    """Resolve a flag or alias to its canonical :class:`ServerArg`."""
    return _SERVER_ARGS_BY_FLAG.get(name) or _SERVER_ARGS_BY_ALIAS.get(name)


def validate_value(arg: ServerArg, value: str) -> str:
    """Normalize a user-supplied value for ``arg``; raises ``ValueError``.

    A blank value stays blank (the flag is omitted). BOOL accepts
    on/true/1/yes and off/false/0/no. INT/FLOAT are parsed numerically.
    CHOICE must be one of the allowed values.
    """
    v = value.strip()
    if not v:
        return ""
    if arg.kind is ArgKind.BOOL:
        if v.lower() in _TRUE:
            return "on"
        if v.lower() in _FALSE:
            return "off"
        raise ValueError(f"{arg.flag}: expected on/off/true/false/1/0, got '{value}'")
    if arg.kind is ArgKind.INT:
        int(v)  # raises ValueError for non-integers
        return v
    if arg.kind is ArgKind.FLOAT:
        float(v)  # raises ValueError for non-numbers
        return v
    if arg.kind is ArgKind.CHOICE:
        if v not in arg.choices:
            raise ValueError(
                f"{arg.flag}: expected one of {', '.join(arg.choices)}, got '{value}'"
            )
        return v
    return v


def validate_options(options: Mapping[str, str]) -> dict[str, str]:
    """Validate every entry; returns ``{flag: error_message}`` for bad ones."""
    errors: dict[str, str] = {}
    for flag, value in options.items():
        arg = find_arg(flag)
        if arg is None:
            errors[flag] = f"unknown option '{flag}'"
            continue
        try:
            validate_value(arg, str(value))
        except ValueError as exc:
            errors[flag] = str(exc)
    return errors


def count() -> dict[str, int]:
    """Per-section counts for the docs / coverage report."""
    totals: dict[str, int] = {}
    for arg in SERVER_ARGS:
        totals[arg.section] = totals.get(arg.section, 0) + 1
    return totals


__all__ = [
    "DEDICATED_FLAGS",
    "SECTIONS",
    "SERVER_ARGS",
    "ArgKind",
    "ServerArg",
    "count",
    "find_arg",
    "options_to_cli",
    "validate_options",
    "validate_value",
]
