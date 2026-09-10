const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadLinks() {
  const env = { window: {}, URL, URLSearchParams };
  vm.createContext(env);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../public/external-links.js"), "utf8"), env);
  return env.window.TripLinks;
}

const city = { name: "西安市", canonical_name: "西安", map_query: "陕西省西安市" };

function assertAmapSearch(value, { keyword, cityName = "西安", native = "1", view = "list" }) {
  const url = new URL(value);
  assert.equal(url.origin, "https://uri.amap.com");
  assert.equal(url.pathname, "/search");
  assert.equal(url.searchParams.get("keyword"), keyword);
  assert.equal(url.searchParams.get("city"), cityName);
  assert.equal(url.searchParams.get("callnative"), native);
  assert.equal(url.searchParams.get("src"), "travelplanner");
  assert.equal(url.searchParams.get("view"), view);
  return url;
}

test("place search uses canonical city and encodes Chinese and reserved characters once", () => {
  const links = loadLinks();
  const keyword = "茶 & 酒（南门店）+ A/B #1?";
  assertAmapSearch(links.amapSearch(city, keyword), { keyword });
  assertAmapSearch(links.amapSearch("北京", "故宫"), { cityName: "北京", keyword: "故宫" });
});

test("web fallback retains the same place and city while disabling native launch", () => {
  const links = loadLinks();
  const native = new URL(links.amapSearch(city, "陕西历史博物馆"));
  const web = assertAmapSearch(links.amapSearch(city, "陕西历史博物馆", { webOnly: true }), {
    keyword: "陕西历史博物馆", native: "0",
  });
  native.searchParams.set("callnative", "0");
  assert.equal(web.href, native.href);
});

test("city maps use real API city metadata with map view and no inferred coordinates", () => {
  const links = loadLinks();
  const mapped = assertAmapSearch(links.amapCity(city), { keyword: "陕西省西安市", view: "map" });
  assert.equal(mapped.searchParams.has("position"), false);
  assertAmapSearch(links.amapCity({ name: "手动旅游城市" }), {
    cityName: "手动旅游城市", keyword: "手动旅游城市", view: "map",
  });
  assertAmapSearch(links.amapCity("广州", { webOnly: true }), {
    cityName: "广州", keyword: "广州", native: "0", view: "map",
  });
});

test("city maps follow metadata after city switch or administrator rename", () => {
  const links = loadLinks();
  const selected = { name: "北京", canonical_name: "北京", map_query: "北京市" };
  assertAmapSearch(links.amapCity(selected), { cityName: "北京", keyword: "北京市", view: "map" });
  Object.assign(selected, { name: "广州", canonical_name: "广州", map_query: "广东省广州市" });
  assertAmapSearch(links.amapCity(selected), { cityName: "广州", keyword: "广东省广州市", view: "map" });
});

test("empty or unsafe saved navigation falls back to the current item name", () => {
  const links = loadLinks();
  const invalid = [undefined, "", "  ", "not a URL", "javascript:alert(1)", "data:text/html,hello",
    "ftp://example.com/path", "//uri.amap.com/search?keyword=old", "https://user:password@example.com/path",
    "https://user@uri.amap.com/search?keyword=old"];
  for (const navigation_link of invalid) {
    assertAmapSearch(links.attractionUrl({ name: "新景点名称", navigation_link }, city), {
      keyword: "新景点名称",
    });
  }
});

test("legacy frontend generated links are rebuilt for edited names and current cities", () => {
  const links = loadLinks();
  const item = { name: "陕西历史博物馆", navigation_link:
    "https://uri.amap.com/search?keyword=%E5%A4%96%E6%BB%A9&city=%E4%B8%8A%E6%B5%B7&view=map&src=travelplanner&callnative=0" };
  const original = { ...item };
  assertAmapSearch(links.attractionUrl(item, city), { keyword: "陕西历史博物馆" });
  assert.deepEqual(item, original, "rendering must not mutate a persisted record or its version");
});

test("legacy AI links without src are repaired for cached copies and edited drafts", () => {
  const links = loadLinks();
  const item = { name: "新名称 + 新分店", navigation_link:
    "https://uri.amap.com/search?keyword=%E6%97%A7%E5%90%8D%E7%A7%B0&city=%E4%B8%8A%E6%B5%B7&callnative=0" };
  assertAmapSearch(links.attractionUrl(item, city), { keyword: item.name });
  assertAmapSearch(links.attractionUrl(item, { name: "长沙" }), { cityName: "长沙", keyword: item.name });
  assertAmapSearch(links.attractionUrl(item, city, { webOnly: true }), { keyword: item.name, native: "0" });
});

test("custom search targets and additional parameters survive normalization", () => {
  const links = loadLinks();
  const savedLinks = [
    "https://uri.amap.com/search?keyword=custom+venue&city=Shanghai",
    "https://uri.amap.com/search?keyword=custom+venue&city=Shanghai&callnative=0&src=other-app",
    "https://uri.amap.com/search?keyword=custom+venue&city=Shanghai&callnative=0&src=travelplanner&custom=keep-me",
    "https://uri.amap.com/search?keyword=custom+venue&city=Shanghai&callnative=0&extra=keep-me",
  ];
  for (const navigation_link of savedLinks) {
    const before = new URL(navigation_link);
    const native = new URL(links.attractionUrl({ name: "不同的景点名", navigation_link }, city));
    assert.equal(native.searchParams.get("keyword"), "custom venue");
    assert.equal(native.searchParams.get("city"), "Shanghai");
    assert.equal(native.searchParams.get("callnative"), "1");
    before.searchParams.set("callnative", "1");
    assert.equal(native.href, before.href);
  }
});

test("saved coordinates routes and POI targets are kept for both launch modes", () => {
  const links = loadLinks();
  const savedLinks = [
    "https://uri.amap.com/marker?position=121.5%2C31.2&name=%E6%8C%87%E5%AE%9A%E5%85%A5%E5%8F%A3&coordinate=gaode&callnative=0",
    "https://uri.amap.com/navigation?to=121.5%2C31.2%2Cdestination&mode=walk&callnative=0",
    "https://uri.amap.com/route?from=121.1%2C31.1&to=121.5%2C31.2&mode=car",
    "https://uri.amap.com/detail?poiid=B0FFTEST01&callnative=0#entry",
  ];
  for (const navigation_link of savedLinks) {
    for (const webOnly of [false, true]) {
      const expected = new URL(navigation_link);
      expected.searchParams.set("callnative", webOnly ? "0" : "1");
      assert.equal(links.attractionUrl({ name: "当前景点名称", navigation_link }, city, { webOnly }), expected.href);
    }
  }
});

test("third party maps and Amap short links remain untouched", () => {
  const links = loadLinks();
  const savedLinks = ["https://surl.amap.com/abc123", "https://www.amap.com/detail/B0FFTEST01",
    "https://map.baidu.com/search/test?from=travel", "https://example.com/custom-path?q=venue#entry",
    "https://uri.amap.com.example.org/search?keyword=custom&city=elsewhere&callnative=0"];
  for (const navigation_link of savedLinks) {
    assert.equal(links.attractionUrl({ name: "任意名称", navigation_link }, city), navigation_link);
    assert.equal(links.attractionUrl({ name: "任意名称", navigation_link }, city, { webOnly: true }), navigation_link);
  }
});

test("missing city metadata never silently selects Shanghai", () => {
  const links = loadLinks();
  const result = new URL(links.attractionUrl({ name: "测试景点", navigation_link: "" }, ""));
  assert.equal(result.searchParams.get("keyword"), "测试景点");
  assert.notEqual(result.searchParams.get("city"), "上海");
});

test("mobile launch policy detects Android iOS and touch iPad without viewport guessing", () => {
  const links = loadLinks();
  for (const navigator of [
    { userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36" },
    { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)" },
    { userAgent: "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)" },
    { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)", platform: "MacIntel", maxTouchPoints: 5 },
  ]) assert.equal(links.isMobile(navigator), true);
  for (const navigator of [undefined, {}, { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" },
    { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)", platform: "MacIntel", maxTouchPoints: 0 },
    { platform: "MacIntel", maxTouchPoints: 1 }, { innerWidth: 320, maxTouchPoints: 10, platform: "Win32" }]) {
    assert.equal(links.isMobile(navigator), false);
  }
});

test("copyable food search text follows city and item renames and keeps literal special characters", () => {
  const links = loadLinks();
  const item = { name: "茶 & 酒（南门店）+ A/B", where_to_try: "可能尝试多家店的长篇推荐" };
  const original = { ...item };
  assert.equal(links.foodSearchText(city, item), "西安 茶 & 酒（南门店）+ A/B");
  assert.equal(links.foodSearchText({ name: "长沙" }, { ...item, name: "新店名" }), "长沙 新店名");
  assert.equal(links.foodSearchText("", { name: "小吃" }), "小吃");
  assert.equal(links.foodSearchText("广州", {}), "广州");
  assert.equal(links.foodSearchText("", {}), "");
  assert.deepEqual(item, original);
});
