"""
电商API MCP Server — 万邦OneBound

通过万邦API搜索淘宝/1688等平台的商品、查价格。

MCP连接方式：
  transport: streamable-http
  url: http://<your-server>:8092/mcp

工具列表：
  - shop_search: 搜索商品
  - shop_detail: 查看商品详情/价格
  - shop_search_shop: 搜索店铺
  - shop_search_suggest: 搜索建议/联想词
"""

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = "https://api-gw.onebound.cn"
API_KEY = "t8522894055"
API_SECRET = "40558292"

mcp = FastMCP(
    "Shop",
    host="0.0.0.0",
    port=8092,
    instructions="""电商商品搜索MCP。
用 shop_search 搜商品，shop_detail 看详情和价格。
支持淘宝、1688等平台。"""
)


async def _api_call(platform: str, api_name: str, params: dict) -> dict:
    url = f"{API_BASE}/{platform}/{api_name}"
    params["key"] = API_KEY
    params["secret"] = API_SECRET
    params["lang"] = "zh-CN"
    params["result_type"] = "json"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        return resp.json()


@mcp.tool()
async def shop_search(q: str, platform: str = "taobao", page: int = 1,
                      sort: str = "", start_price: float = 0,
                      end_price: float = 0, page_size: int = 20) -> str:
    """搜索商品。

    Args:
        q: 搜索关键词
        platform: 平台，taobao/1688，默认taobao
        page: 页码，默认1
        sort: 排序方式，可选值：
            默认综合排序(留空)
            bid — 人气排序
            price_asc — 价格从低到高
            price_desc — 价格从高到低
            sale — 销量排序
        start_price: 最低价格（可选）
        end_price: 最高价格（可选）
        page_size: 每页数量，默认20，最大40
    """
    params = {"q": q, "page": str(page), "page_size": str(min(page_size, 40))}
    if sort:
        params["sort"] = sort
    if start_price > 0:
        params["start_price"] = str(start_price)
    if end_price > 0:
        params["end_price"] = str(end_price)

    try:
        data = await _api_call(platform, "item_search", params)

        if data.get("error"):
            return f"❌ 搜索失败: {data.get('error', '未知错误')} — {data.get('reason', '')}"

        items = data.get("items", {}).get("item", [])
        if not items:
            return f"🔍 搜索 \"{q}\" — 没有找到商品"

        total = data.get("items", {}).get("total_results", len(items))
        page_count = data.get("items", {}).get("pagecount", "?")

        lines = [f"🛒 搜索 \"{q}\" — 共{total}件，第{page}/{page_count}页", ""]

        for i, item in enumerate(items[:20], 1):
            title = item.get("title", "").replace("<span class=H>", "").replace("</span>", "")
            price = item.get("price", "?")
            sales = item.get("sales", item.get("sold", ""))
            nick = item.get("nick", item.get("seller_nick", ""))
            num_iid = item.get("num_iid", "")
            pic = item.get("pic_url", "")

            lines.append(f"{i}. {title}")
            lines.append(f"   💰 ¥{price}  📦 销量:{sales}  🏪 {nick}")
            lines.append(f"   ID: {num_iid}")
            lines.append("")

        lines.append(f"💡 用 shop_detail(num_iid=\"商品ID\") 查看详情")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 搜索出错: {e}"


@mcp.tool()
async def shop_detail(num_iid: str, platform: str = "taobao") -> str:
    """查看商品详情和价格。

    Args:
        num_iid: 商品ID（从shop_search获取）
        platform: 平台，taobao/1688，默认taobao
    """
    params = {"num_iid": num_iid}

    try:
        data = await _api_call(platform, "item_get", params)

        if data.get("error"):
            return f"❌ 查询失败: {data.get('error', '未知错误')} — {data.get('reason', '')}"

        item = data.get("item", {})
        if not item:
            return f"❌ 找不到商品 {num_iid}"

        title = item.get("title", "未知")
        price = item.get("price", "?")
        orginal_price = item.get("orginal_price", item.get("original_price", ""))
        sales = item.get("sales", item.get("sold", "0"))
        nick = item.get("nick", item.get("seller_nick", ""))
        location = item.get("location", "")
        desc = item.get("desc_short", item.get("desc", ""))
        detail_url = item.get("detail_url", "")

        lines = [
            f"🛍️ 商品详情",
            f"标题: {title}",
            f"💰 价格: ¥{price}",
        ]
        if orginal_price and orginal_price != price:
            lines.append(f"💲 原价: ¥{orginal_price}")
        lines.append(f"📦 销量: {sales}")
        lines.append(f"🏪 店铺: {nick}")
        if location:
            lines.append(f"📍 发货地: {location}")
        lines.append(f"🔗 ID: {num_iid}")

        skus = item.get("skus", {}).get("sku", [])
        if skus:
            lines.append("")
            lines.append(f"📋 规格 ({len(skus)}种):")
            for sku in skus[:10]:
                sku_name = sku.get("properties_name", sku.get("name", ""))
                sku_price = sku.get("price", "?")
                sku_name_clean = sku_name.split(":")[-1] if ":" in sku_name else sku_name
                lines.append(f"  • {sku_name_clean} — ¥{sku_price}")
            if len(skus) > 10:
                lines.append(f"  ... 还有{len(skus)-10}种规格")

        if detail_url:
            lines.append(f"\n🔗 {detail_url}")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ 查询出错: {e}"


@mcp.tool()
async def shop_search_shop(q: str, platform: str = "taobao", page: int = 1) -> str:
    """搜索店铺。

    Args:
        q: 店铺名关键词
        platform: 平台，默认taobao
        page: 页码，默认1
    """
    params = {"q": q, "page": str(page)}

    try:
        data = await _api_call(platform, "item_search_shop", params)

        if data.get("error"):
            return f"❌ 搜索失败: {data.get('error', '未知错误')}"

        shops = data.get("items", {}).get("item", [])
        if not shops:
            return f"🏪 搜索 \"{q}\" — 没有找到店铺"

        lines = [f"🏪 店铺搜索 \"{q}\"", ""]

        for i, shop in enumerate(shops[:10], 1):
            name = shop.get("nick", shop.get("title", ""))
            shop_id = shop.get("seller_id", shop.get("user_id", ""))
            location = shop.get("location", "")
            lines.append(f"{i}. {name}")
            if location:
                lines.append(f"   📍 {location}")
            if shop_id:
                lines.append(f"   ID: {shop_id}")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ 搜索出错: {e}"


@mcp.tool()
async def shop_search_suggest(q: str, platform: str = "taobao") -> str:
    """获取搜索建议/联想词。

    Args:
        q: 关键词
        platform: 平台，默认taobao
    """
    params = {"q": q}

    try:
        data = await _api_call(platform, "item_search_suggest", params)

        if data.get("error"):
            return f"❌ 获取建议失败: {data.get('error', '未知错误')}"

        items = data.get("items", {}).get("item", [])
        if not items:
            return f"💡 \"{q}\" 没有搜索建议"

        lines = [f"💡 \"{q}\" 的搜索建议:", ""]
        for item in items[:10]:
            keyword = item.get("keyword", item.get("title", ""))
            lines.append(f"  • {keyword}")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取建议出错: {e}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
