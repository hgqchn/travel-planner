"use strict";

// Use providers' HTTPS entry points: the destination handles App opening and
// web fallback. A normal link click preserves the browser's user activation.
window.TripLinks = (() => {
  const text = (value) => typeof value === "string" ? value.trim() : "";
  const cityName = (city) => typeof city === "string" ? text(city) : text(city?.canonical_name) || text(city?.name);

  function safeUrl(value) {
    if (!text(value)) return null;
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url : null;
    } catch { return null; }
  }

  function amapSearch(city, keyword, { webOnly = false, view = "list" } = {}) {
    const url = new URL("https://uri.amap.com/search");
    url.searchParams.set("keyword", text(keyword) || cityName(city) || "地点");
    if (cityName(city)) url.searchParams.set("city", cityName(city));
    url.searchParams.set("view", view === "map" ? "map" : "list");
    url.searchParams.set("src", "travelplanner");
    url.searchParams.set("callnative", webOnly ? "0" : "1");
    return url.href;
  }

  function amapCity(city, { webOnly = false } = {}) {
    return amapSearch(city, text(city?.map_query) || cityName(city), { webOnly, view: "map" });
  }

  function generatedSearch(url) {
    if (url.pathname !== "/search" || url.hash) return false;
    const keys = [...url.searchParams.keys()];
    if (new Set(keys).size !== keys.length) return false;
    const allowed = ["keyword", "city", "view", "src", "callnative"];
    const marked = url.searchParams.get("src") === "travelplanner" && keys.every((key) => allowed.includes(key));
    // Before source tags were added, AI produced exactly these three fields.
    // Other saved links may carry deliberate POI, route or search constraints.
    const legacyAI = keys.length === 3 && ["keyword", "city", "callnative"].every((key) => keys.includes(key));
    return marked || legacyAI;
  }

  function attractionUrl(item, city, { webOnly = false } = {}) {
    const url = safeUrl(item?.navigation_link);
    if (!url) return amapSearch(city, item?.name, { webOnly });
    if (url.hostname !== "uri.amap.com" || url.port) return url.href;
    if (generatedSearch(url)) return amapSearch(city, item?.name, { webOnly });
    url.protocol = "https:";
    url.searchParams.set("callnative", webOnly ? "0" : "1");
    return url.href;
  }

  function isMobile(device = {}) {
    return /Android|iPhone|iPad|iPod|Mobile/i.test(device.userAgent || "") ||
      (device.platform === "MacIntel" && device.maxTouchPoints > 1);
  }

  function foodSearchText(city, item) {
    return [cityName(city), text(item?.name)].filter(Boolean).join(" ");
  }

  return { cityName, amapSearch, amapCity, attractionUrl, isMobile, foodSearchText };
})();
