import os
import re
from typing import Any

import yaml


def parse_filename(
    filename: str,
    default_mode: str = "0600",
    default_uid: int = 100000,
    default_gid: int = 100000,
) -> dict[str, Any]:
    """
    Parses metadata (mode, uid, gid) from filename and produces clean destination filename.

    Examples:
      - config.yaml -> target: config.yaml, mode: 0600, owner: 100000, group: 100000, is_template: False
      - config.0644.yaml -> target: config.yaml, mode: 0644, owner: 100000, group: 100000, is_template: False
      - config.0644.100900.yaml -> target: config.yaml, mode: 0644, owner: 100900, group: 100900, is_template: False
      - config.0644.100900.yaml.j2 -> target: config.yaml, mode: 0644, owner: 100900, group: 100900, is_template: True
      - Caddyfile.0644 -> target: Caddyfile, mode: 0644, owner: 100000, group: 100000, is_template: False
    """
    is_template = False
    clean = filename
    if clean.endswith(".j2"):
        is_template = True
        clean = clean[:-3]

    parts = clean.split(".")
    if len(parts) == 1:
        return {
            "target_name": clean,
            "mode": str(default_mode),
            "owner": default_uid,
            "group": default_gid,
            "is_template": is_template,
        }

    def is_mode_token(tok: str) -> bool:
        return bool(re.match(r"^(0[0-7]{3}|[0-7]{3})$", tok))

    def is_uid_token(tok: str) -> bool:
        if ":" in tok:
            u, g = tok.split(":", 1)
            return u.isdigit() and g.isdigit()
        return (
            tok.isdigit() and (tok == "0" or not tok.startswith("0")) and len(tok) >= 4
        )

    mode = str(default_mode)
    owner = default_uid
    group = default_gid

    # Detect extension: if the last component is not a mode or UID, treat it as file extension
    if not is_mode_token(parts[-1]) and not is_uid_token(parts[-1]):
        ext = parts[-1]
        tokens = parts[:-1]
    else:
        ext = ""
        tokens = parts[:]

    if len(tokens) == 1:
        target_name = tokens[0] + ("." + ext if ext else "")
        return {
            "target_name": target_name,
            "mode": mode,
            "owner": owner,
            "group": group,
            "is_template": is_template,
        }

    extracted_mode = None
    extracted_uid = None
    consumed = 0

    # Pattern: <name>.<mode>.<uid>
    if len(tokens) >= 3 and is_mode_token(tokens[-2]) and is_uid_token(tokens[-1]):
        extracted_mode = tokens[-2]
        extracted_uid = tokens[-1]
        consumed = 2
    # Pattern: <name>.<mode>
    elif len(tokens) >= 2 and is_mode_token(tokens[-1]):
        extracted_mode = tokens[-1]
        consumed = 1
    # Pattern: <name>.<uid>
    elif len(tokens) >= 2 and is_uid_token(tokens[-1]):
        extracted_uid = tokens[-1]
        consumed = 1

    base_tokens = tokens[:-consumed] if consumed > 0 else tokens

    if extracted_mode:
        m = extracted_mode
        if len(m) == 3:
            m = "0" + m
        mode = m

    if extracted_uid:
        if ":" in extracted_uid:
            u, g = extracted_uid.split(":", 1)
            owner = int(u)
            group = int(g)
        else:
            owner = int(extracted_uid)
            group = int(extracted_uid)

    target_name = ".".join(base_tokens) + ("." + ext if ext else "")

    return {
        "target_name": target_name,
        "mode": mode,
        "owner": owner,
        "group": group,
        "is_template": is_template,
    }


def _extract_volumes_from_compose(
    compose_path: str,
    volumes_base: str,
    known_file_paths: set[str],
    env_vars: dict[str, str],
) -> set[str]:
    """
    Parses docker-compose file and extracts host directory paths located under volumes_base.
    """
    dirs_to_create: set[str] = set()

    if not os.path.isfile(compose_path):
        return dirs_to_create

    try:
        with open(compose_path, encoding="utf-8") as f:
            content = f.read()

        # Substitute environment variables like ${VAR:-default} or ${VAR}
        def _replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            default_val = match.group(2) if match.group(2) is not None else ""
            return env_vars.get(var_name, default_val)

        resolved_content = re.sub(
            r"\$\{([^}:]+)(?::-([^}]*))?\}", _replace_var, content
        )
        compose_data = yaml.safe_load(resolved_content) or {}
    except OSError, yaml.YAMLError:
        return dirs_to_create

    if not isinstance(compose_data, dict):
        return dirs_to_create

    services = compose_data.get("services", {})
    if not isinstance(services, dict):
        return dirs_to_create

    normalized_base = os.path.abspath(volumes_base)

    for service_conf in services.values():
        if not isinstance(service_conf, dict):
            continue

        raw_volumes = service_conf.get("volumes", [])
        if not isinstance(raw_volumes, list):
            continue

        for vol in raw_volumes:
            host_path: str = ""
            if isinstance(vol, str):
                parts = vol.split(":")
                host_path = parts[0].strip()
            elif isinstance(vol, dict) and vol.get("type") == "bind":
                host_path = str(vol.get("source", "")).strip()

            if not host_path or not host_path.startswith("/"):
                continue

            abs_host_path = os.path.abspath(host_path)

            # Check if this path resides within volumes_base
            if abs_host_path == normalized_base or abs_host_path.startswith(
                normalized_base + os.sep
            ):
                # Check if it points to a known file or has a file extension
                _, ext = os.path.splitext(abs_host_path)
                is_file = abs_host_path in known_file_paths or (
                    ext != "" and len(ext) <= 6 and not ext.isdigit()
                )

                target_dir = (
                    os.path.dirname(abs_host_path) if is_file else abs_host_path
                )

                # Add target directory and all intermediate directories up to volumes_base
                curr = target_dir
                while (
                    curr.startswith(normalized_base + os.sep) or curr == normalized_base
                ):
                    dirs_to_create.add(curr)
                    if curr == normalized_base:
                        break
                    curr = os.path.dirname(curr)

    return dirs_to_create


def parse_stack_files(
    stack_dir: str,
    default_mode: str = "0600",
    default_uid: int = 100000,
    default_gid: int = 100000,
    volumes_base: str = "/mnt/docker-volumes",
) -> dict[str, Any]:
    """
    Scans a compose stack directory and returns metadata for deployment:
    - compose_file path
    - env_file dict {src, is_template} (if any)
    - volume_dirs: all directories to create in /mnt/docker-volumes (from compose + configs)
    - list of parsed configuration files with mode, owner, group and destination path
    """
    stack_name = os.path.basename(os.path.abspath(stack_dir))
    compose_file = None
    env_file = None
    config_files = []
    volume_dirs: set[str] = set()

    compose_names = {
        "docker-compose.yaml",
        "docker-compose.yml",
        "compose.yaml",
        "compose.yml",
    }

    normalized_volumes_base = os.path.abspath(volumes_base)

    # 1. Walk directory to find compose, .env and config files
    for root, _dirs, files in os.walk(stack_dir):
        rel_root = os.path.relpath(root, stack_dir)

        for f in sorted(files):
            if f in (".gitkeep", ".DS_Store") or f.startswith(".git"):
                continue

            full_path = os.path.join(root, f)

            # Top-level compose file
            if rel_root == "." and f in compose_names:
                compose_file = full_path
                continue

            # Top-level .env template (.env.j2) or static .env
            if rel_root == "." and f in (".env", ".env.j2"):
                if env_file is None or f.endswith(".j2"):
                    env_file = {
                        "src": full_path,
                        "is_template": f.endswith(".j2"),
                    }
                continue

            # Additional configuration file
            meta = parse_filename(
                f,
                default_mode=default_mode,
                default_uid=default_uid,
                default_gid=default_gid,
            )

            # Determine container_name and inner file path
            # Destination path: <volumes_base>/<stack_name>/<container_name>/config/<file_subpath>
            if rel_root == ".":
                container_name = stack_name
                file_subpath = meta["target_name"]
            else:
                rel_parts = rel_root.split(os.sep)
                container_name = rel_parts[0]
                inner_parts = rel_parts[1:]
                # Avoid duplicating 'config' if the source folder is already named config
                if inner_parts and inner_parts[0] == "config":
                    inner_parts = inner_parts[1:]
                if inner_parts:
                    file_subpath = os.path.normpath(
                        os.path.join(*inner_parts, meta["target_name"])
                    )
                else:
                    file_subpath = meta["target_name"]

            dest_path = os.path.normpath(
                os.path.join(
                    normalized_volumes_base,
                    stack_name,
                    container_name,
                    "config",
                    file_subpath,
                )
            )

            # Track full directory hierarchy for config files
            curr = os.path.dirname(dest_path)
            while (
                curr.startswith(normalized_volumes_base + os.sep)
                or curr == normalized_volumes_base
            ):
                volume_dirs.add(curr)
                if curr == normalized_volumes_base:
                    break
                curr = os.path.dirname(curr)

            config_files.append(
                {
                    "src": full_path,
                    "dest_path": dest_path,
                    "mode": meta["mode"],
                    "owner": meta["owner"],
                    "group": meta["group"],
                    "is_template": meta["is_template"],
                }
            )

    # 2. Parse compose file to discover all volume bind mounts (e.g. data, cache, etc.)
    if compose_file:
        known_files = {cf["dest_path"] for cf in config_files}
        env_vars = {
            "STACK_NAME": stack_name,
            "COMPOSE_PROJECT_NAME": stack_name,
            "VOLUMES_DIR": normalized_volumes_base,
        }
        compose_dirs = _extract_volumes_from_compose(
            compose_file,
            normalized_volumes_base,
            known_files,
            env_vars,
        )
        volume_dirs.update(compose_dirs)

    return {
        "name": stack_name,
        "compose_file": compose_file,
        "env_file": env_file,
        "volume_dirs": sorted(volume_dirs, key=lambda x: (len(x.split(os.sep)), x)),
        "config_files": sorted(config_files, key=lambda x: x["dest_path"]),
    }


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "parse_filename": parse_filename,
            "parse_stack_files": parse_stack_files,
        }
