"""Tool execution and prompt formatting for agent function calling."""
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes HTTP tool calls against configured endpoints."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self):
        await self._client.aclose()

    async def execute(
        self, tool_def: dict, arguments: dict, variables: dict | None = None
    ) -> dict[str, Any]:
        variables = variables or {}
        url = self._substitute(tool_def["endpoint"], variables)
        url = self._substitute_path(url, arguments)
        headers = {
            k: self._substitute(v, variables)
            for k, v in tool_def.get("headers", {}).items()
        }
        method = tool_def.get("method", "POST").upper()
        timeout = tool_def.get("timeout_seconds", 10)

        try:
            if method == "GET":
                resp = await self._client.get(url, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = await self._client.post(url, json=arguments, headers=headers, timeout=timeout)
            elif method == "PUT":
                resp = await self._client.put(url, json=arguments, headers=headers, timeout=timeout)
            elif method == "DELETE":
                resp = await self._client.delete(url, headers=headers, timeout=timeout)
            else:
                return {"error": f"Unsupported method: {method}"}

            try:
                data = resp.json()
            except Exception:
                data = resp.text

            logger.info("Tool %s: %s %s → %s", tool_def["name"], method, url, resp.status_code)
            return {"status": resp.status_code, "data": data}

        except httpx.TimeoutException:
            logger.error("Tool %s timed out after %ss", tool_def["name"], timeout)
            return {"error": f"Tool timed out after {timeout}s"}
        except Exception as e:
            logger.error("Tool %s failed: %s", tool_def["name"], e)
            return {"error": str(e)}

    @staticmethod
    def _substitute(text: str, variables: dict) -> str:
        """Replace {{key}} with value. Single pass only — no re-expansion."""
        for key, value in variables.items():
            text = text.replace(f"{{{{{key}}}}}", str(value))
        return text

    @staticmethod
    def _substitute_path(url: str, arguments: dict) -> str:
        """Replace {key} path params in URL with argument values."""
        for key, value in arguments.items():
            url = url.replace(f"{{{key}}}", str(value))
        return url


def format_tools_prompt(tools: list[dict]) -> str:
    """Generate Spanish-language tool instruction block for system prompt injection."""
    if not tools:
        return ""

    lines = [
        "",
        "---",
        "HERRAMIENTAS DISPONIBLES:",
        "Cuando necesites usar una herramienta, responde ÚNICAMENTE con un JSON en este formato exacto:",
        '{"tool": "nombre_herramienta", "arguments": {"param1": "valor1"}}',
        "",
        "REGLAS CRÍTICAS para herramientas:",
        "- Tu respuesta ENTERA debe ser SOLO el JSON. Nada más.",
        "- NO escribas texto antes o después del JSON.",
        "- NO combines texto normal con una llamada a herramienta.",
        "",
        'Ejemplo correcto: {"tool": "end_call", "arguments": {"reason": "Gracias por llamar, hasta luego."}}',
        'Ejemplo INCORRECTO: ¡De nada! end_call{"reason": "..."}',
        "",
    ]

    for tool in tools:
        name = tool["name"]
        desc = tool.get("description", "")
        params = tool.get("parameters", {}).get("properties", {})
        required = tool.get("parameters", {}).get("required", [])

        param_parts = []
        for pname, pdef in params.items():
            req_marker = " (requerido)" if pname in required else ""
            pdesc = pdef.get("description", "")
            param_parts.append(f"    - {pname}: {pdesc}{req_marker}")

        lines.append(f"• {name}: {desc}")
        if param_parts:
            lines.append("  Parámetros:")
            lines.extend(param_parts)
        lines.append("")

    # Built-in tools
    lines.append("• end_call: Terminar la llamada después de que el usuario se despida.")
    lines.append("  Usar cuando: el usuario dice adiós, gracias por todo, hasta luego, ya no necesito nada.")
    lines.append("  NO usar para: responder preguntas o dar información. El campo 'reason' es SOLO una frase corta de despedida.")
    lines.append("  Parámetros:")
    lines.append("    - reason: Frase corta de despedida, máximo 15 palabras (requerido)")
    lines.append("")
    lines.append("• transfer_call: Transferir la llamada a otro número")
    lines.append("  Parámetros:")
    lines.append("    - target_number: Número destino E.164 (requerido)")
    lines.append("    - reason: Motivo breve (requerido)")
    lines.append("")

    return "\n".join(lines)
