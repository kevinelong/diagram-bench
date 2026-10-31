//! The ComfyUI graph, built the way `zimage_gen.workflow()` builds it: same node ids, same loaders, same values.
//!
//! Only text-to-image is wired here (what the prototype's one prompt box asks for), so the graph is the branch of the
//! Python function that runs when there is no base image, mask or outpaint:
//!
//! `1` UNETLoader/UnetLoaderGGUF · `2` CLIPLoader/CLIPLoaderGGUF · `3` VAELoader · `4`/`5` CLIPTextEncode
//! `6` EmptySD3LatentImage → `7` KSampler → `8` VAEDecode → `9` SaveImage
//!
//! The profiles are the Python `MODELS` dict's values, copied rather than re-typed: a GGUF profile names a different
//! unet/clip node than a safetensors one, and `lumina2` versus `flux2` is the clip *type* a loader is told to use.

/// One checkpoint profile: the fields of a `zimage_gen.MODELS` entry this prototype needs.
pub struct Profile {
    pub name: &'static str,
    pub unet: &'static str,
    pub unet_node: &'static str,
    pub clip: &'static str,
    pub clip_node: &'static str,
    pub clip_type: &'static str,
    pub vae: &'static str,
    /// the count that model is distilled or documented for (`MODELS[...]['steps']`)
    pub steps: u32,
    pub cfg: f64,
}

pub const DEFAULT_MODEL: &str = "flux2 klein 9b uncensored";

pub const MODELS: &[Profile] = &[
    // z-image turbo is nvfp4; its encoder must be loaded as `lumina2` - the flux encoder in the same folder will not do
    Profile {
        name: "z-image turbo",
        unet: "z_image_turbo_nvfp4.safetensors",
        unet_node: "UNETLoader",
        clip: "qwen_3_4b_fp4_mixed.safetensors",
        clip_node: "CLIPLoader",
        clip_type: "lumina2",
        vae: "ae.safetensors",
        steps: 9,
        cfg: 1.0,
    },
    // the 9B diffusion with the 9B uncensored encoder, both GGUF (UnetLoaderGGUF from ComfyUI-GGUF)
    Profile {
        name: "flux2 klein 9b uncensored",
        unet: "flux-2-klein-9b-Q4_K_M.gguf",
        unet_node: "UnetLoaderGGUF",
        clip: "flux2-klein-9b-uncensored-te-q5_k_m.gguf",
        clip_node: "CLIPLoaderGGUF",
        clip_type: "flux2",
        vae: "flux2-vae.safetensors",
        steps: 4,
        cfg: 1.0,
    },
];

/// The profile `name`, or the default one: an unknown name is the default model, as in the Python picker.
pub fn profile(name: &str) -> &'static Profile {
    MODELS
        .iter()
        .find(|p| p.name == name)
        .or_else(|| MODELS.iter().find(|p| p.name == DEFAULT_MODEL))
        .expect("MODELS carries the default profile")
}

pub const SAMPLER: &str = "euler";
pub const SCHEDULER: &str = "simple";

/// The text-to-image graph: `{"prompt": <this>}` is what `POST /prompt` wants.
pub fn workflow(
    prompt: &str,
    width: i32,
    height: i32,
    steps: u32,
    cfg: f64,
    seed: u64,
    prefix: &str,
    prof: &Profile,
) -> serde_json::Value {
    use serde_json::json;

    // UNETLoader requires weight_dtype; the GGUF loader does not accept it
    let mut unet_inputs = json!({ "unet_name": prof.unet });
    if prof.unet_node == "UNETLoader" {
        unet_inputs["weight_dtype"] = json!("default");
    }
    // CLIPLoader takes a safetensors encoder, CLIPLoaderGGUF a .gguf one: same inputs either way
    let clip_node = json!({ "class_type": prof.clip_node,
                            "inputs": { "clip_name": prof.clip, "type": prof.clip_type } });

    json!({
        "1": { "class_type": prof.unet_node, "inputs": unet_inputs },
        "2": clip_node,
        "3": { "class_type": "VAELoader", "inputs": { "vae_name": prof.vae } },
        "4": { "class_type": "CLIPTextEncode", "inputs": { "text": prompt, "clip": ["2", 0] } },
        "5": { "class_type": "CLIPTextEncode", "inputs": { "text": "", "clip": ["2", 0] } },
        "6": { "class_type": "EmptySD3LatentImage",
               "inputs": { "width": width, "height": height, "batch_size": 1 } },
        "7": { "class_type": "KSampler",
               "inputs": { "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0],
                           "latent_image": ["6", 0], "seed": seed, "steps": steps, "cfg": cfg,
                           "sampler_name": SAMPLER, "scheduler": SCHEDULER, "denoise": 1.0 } },
        "8": { "class_type": "VAEDecode", "inputs": { "samples": ["7", 0], "vae": ["3", 0] } },
        "9": { "class_type": "SaveImage", "inputs": { "images": ["8", 0], "filename_prefix": prefix } }
    })
}
