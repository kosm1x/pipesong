"""Tool execution and prompt formatting for agent function calling."""
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes HTTP tool calls against configured endpoints."""

    async def execute(
        self, tool_def: dict, arguments: dict, variables: dict | None = None
    ) -> dict[str, Any]:
        variables = variables or {}
        url = self._substitute(tool_def["endpoint"], {**variables, **arguments})
        headers = {
            k: self._substitute(v, variables)
            for k, v in tool_def.get("headers", {}).items()
        }
        method = tool_def.get("method", "POST").upper()
        timeout = tool_def.get("timeout_seconds", 10)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                elif method == "POST":
                    resp = await client.post(url, json=arguments, headers=headers)
                elif method == "PUT":
                    resp = await client.put(url, json=arguments, headers=headers)
                elif method == "DELETE":
                    resp = await client.delete(url, headers=headers)
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
        for key, value in variables.items():
            text = text.replace(f"{{{{{key}}}}}", str(value))
            text = text.replace(f"{{{key}}}", str(value))  # also {key} for URL path params
        return text


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
        "NO agregues texto antes ni después del JSON cuando uses una herramienta.",
        "Si NO necesitas una herramienta, responde normalmente en español.",
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

    # Always include built-in tools
    lines.append("• end_call: Terminar la llamada de forma educada cuando el usuario quiere colgar")
    lines.append("  Parámetros:")
    lines.append("    - reason: Mensaje de despedida (requerido)")
    lines.append("")
    lines.append("• transfer_call: Transferir la llamada a otro número de teléfono")
    lines.append("  Parámetros:")
    lines.append("    - target_number: Número de teléfono destino en formato E.164 (requerido)")
    lines.append("    - reason: Motivo de la transferencia (requerido)")
    lines.append("")

    return "\n".join(lines)
