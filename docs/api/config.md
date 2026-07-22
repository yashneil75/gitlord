# Config API

```python
from gitlord import Config, load_config
```

Configuration loading from files, environment variables, and dicts.

## `load_config(path=None) -> Config`

Load configuration with priority: explicit path > `gitlord.json` > environment variables.

```python
from gitlord import load_config

config = load_config()                    # auto-detect
config = load_config("custom-config.json")  # explicit path
```

## `Config` Class

### Constructor

```python
Config(session: SessionConfig | None = None)
```

### Class Methods

#### `Config.from_dict(data) -> Config`

Load from a dictionary.

```python
config = Config.from_dict({
    "session": {
        "agent": {"model": "gpt-4o"},
        "log_repo_path": "log"
    }
})
```

---

#### `Config.from_file(path) -> Config`

Load from a JSON file.

```python
config = Config.from_file("gitlord.json")
```

---

#### `Config.from_env() -> Config`

Load from environment variables.

```python
# GITLORD_MODEL, GITLORD_LOG_REPO, GITLORD_WORKSPACE_REPO
config = Config.from_env()
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `session` | `SessionConfig` | The session configuration |
