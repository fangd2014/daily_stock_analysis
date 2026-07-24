# Cron does not load interactive zsh configuration. Extract only the API key instead of executing the whole file.
if [[ -z "${DEEPSEEK_API_KEY:-}" && -r "${HOME}/.zshrc" ]]; then
    DEEPSEEK_API_KEY=$(
        /usr/bin/sed -nE \
            "/^[[:space:]]*(export[[:space:]]+)?DEEPSEEK_API_KEY[[:space:]]*=/ {
                s/^[[:space:]]*(export[[:space:]]+)?DEEPSEEK_API_KEY[[:space:]]*=[[:space:]]*['\"]?([A-Za-z0-9._-]+).*/\\2/p
                q
            }" \
            "${HOME}/.zshrc"
    )
    export DEEPSEEK_API_KEY
fi
