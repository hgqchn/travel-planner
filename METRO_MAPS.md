# 高清轨道交通图：来源、覆盖与更新

核验与下载日期：2026-09-10。已下载 **54 个城市条目、51 张不同线路图**，当前版本文件合计约 **76.3 MiB**。广州/佛山、郑州/许昌、台北/新北使用各自的共网图。

按中国城市轨道交通协会截至2025年底的58个内地运营城市核对，本次覆盖47个内地城市，另收录香港、澳门和台湾地区7个城市。尚有11个内地城市缺少已核验许可的高清整网图，因此本目录不宣称已经完整覆盖全国所有城轨系统。

当前精选列表中的北京、上海、深圳、广州、西安、洛阳、长沙、重庆、成都、呼和浩特均可在交通页直接查看。手动添加已收录城市时，按城市名称自动匹配，无需再次配置图片。呼伦贝尔、阿尔山、满洲里、鄂尔多斯不套用其他城市的图。

## 图源和清晰度

文件来自 Wikimedia Commons；每张图下方保留原作者署名、原文件页、许可链接、源文件修订日期和最后检查时间。大多数采用 SVG 矢量图，少量使用较新的高清 PNG；SVG 尺寸是原始画布，放大时仍按矢量重绘。位图的尺寸见下表，放大超过原有像素后不会产生新细节。西安、济南的超大位图使用 Commons 提供的高清缩放版本，避免在手机上解码数亿像素的原图。

源文件修订日期只表示作者最近更新了这个文件，不能保证补齐当天所有新线。部分图可能带有规划或建设线路，需结合图例区分。出行以轨道交通运营方公告为准。没有将明确以未来规划为主题的图作为运营整网图。

## 页面更新方式

在交通页点击“检查并更新线路图”。后端从目录中指定的 Commons 文件查询版本；未变化时只记录检查时间，有变化时下载、验证并原子替换当前版本。任务在后台执行，页面显示进度；失败仍可查看旧图。正在放大查看的图保持原版本，关闭后重新打开即可查看新图。

同一部署的所有项目共享线路图缓存，每城5分钟内限一次检查，最多2个并发下载。查看页面仅加载本地文件，不会每次访问都向外网下载。图片已内置在 Docker 镜像中，首次启动可直接查看。在线更新需要服务器能访问 commons.wikimedia.org、upload.wikimedia.org 和 thumb.wikimedia.org 的 HTTPS 服务；网络不可达不影响已保存图片。

按钮只检查 `metro_sources.json` 中固定文件的新版本。若作者改用另一个文件名，需要维护该目录并重新发布程序；按钮不能自行寻找、评估或补绘新线路。

## 文件、部署与维护

- 内置资源：`public/assets/metro/`；`index.json` 和每城 JSON 保存来源与许可元数据。
- 运行时缓存：`$TRIP_DATA_DIR/metro_maps/`；本机预览为 `data/preview/metro_maps/`，Docker 容器内为 `/data/metro_maps/`。随数据卷持久化。
- 原图按内容哈希命名，更新保留旧版本，已打开的链接不会立即失效。长期维护可在停服后清理不再引用的旧图。
- 现有 SQLite 备份脚本只备份业务数据库；更新后的图若也需离线备份，应同时保存上述缓存目录，或从图源重新下载。

维护人员在项目根目录确认 Python 3.9+ 后运行（无额外依赖）：

```sh
# 下载/检查目录中全部图源，写入内置资源
python3 -B metro_maps.py
# 只检查指定城市
python3 -B metro_maps.py --cities beijing shanghai
# 更新运行时缓存；目录需与实际部署的数据卷一致
python3 -B metro_maps.py --output /data/metro_maps --cities beijing
```

更新内置文件后重建 Docker 镜像并重建容器。不要在同一目录同时运行多个批量下载命令。每次升级或更换图源时保留来源、作者与对应许可说明；台北/新北采用原图声明的署名许可，完整条件见原文件许可段。

## 已下载清单

| 城市 | 图源/许可 | 格式和尺寸 | 源文件修订 | 作者 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 北京 | [原文件](https://commons.wikimedia.org/wiki/File:Beijing_Subway_System_Map_zh.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 2400×2400 | 2026-06-29 | Painjet |  |
| 上海 | [原文件](https://commons.wikimedia.org/wiki/File:Shanghai_Metro_Linemap.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 4600×2843 | 2025-12-27 | Yveltal |  |
| 深圳 | [原文件](https://commons.wikimedia.org/wiki/File:Shenzhen_Metro_%28Rapid_Transit%29_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 6124×3431 | 2026-07-22 | Wahsaw |  |
| 广州 | [原文件](https://commons.wikimedia.org/wiki/File:Guangzhou-Foshan_Metro_Diagram_by_Tim.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 2848×2054 | 2026-07-30 | Tim Wu |  |
| 西安 | [原文件](https://commons.wikimedia.org/wiki/File:Xi%27an_metro_map_2025.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 3840×3201 | 2025-01-06 | Nanhuajiaren |  |
| 洛阳 | [原文件](https://commons.wikimedia.org/wiki/File:System_Map_of_Luoyang_Metro.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 4800×3200 | 2024-11-16 | Windmemories |  |
| 长沙 | [原文件](https://commons.wikimedia.org/wiki/File:Changsha_Metro_Linemap.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1658×1317 | 2025-12-25 | Yveltal |  |
| 重庆 | [原文件](https://commons.wikimedia.org/wiki/File:%E9%87%8D%E5%BA%86%E8%BD%A8%E9%81%93%E4%BA%A4%E9%80%9A%E7%BA%BF%E8%B7%AF%E5%9B%BE%EF%BC%882024.12%EF%BC%89.svg) · [Public domain](https://commons.wikimedia.org/wiki/Commons:Licensing) | SVG 2267×2267 | 2026-03-16 | Unknown author Unknown author |  |
| 成都 | [原文件](https://commons.wikimedia.org/wiki/File:Chengdu_Rail_Transit_Network_en.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 4900×4900 | 2025-12-19 | Alan Fan Pei |  |
| 呼和浩特 | [原文件](https://commons.wikimedia.org/wiki/File:Hohhot_Metro_Linemap.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1179×826 | 2023-11-17 | Yveltal |  |
| 天津 | [原文件](https://commons.wikimedia.org/wiki/File:Tianjin_Metro_System_Map_2021.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 5153×3008 | 2026-08-02 | Kmchang28 |  |
| 武汉 | [原文件](https://commons.wikimedia.org/wiki/File:Wuhan_Metro_System_Map_zh.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 3530×3730 | 2026-04-30 | Painjet |  |
| 南京 | [原文件](https://commons.wikimedia.org/wiki/File:Nanjing_Metro_Network.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 4300×4300 | 2026-04-21 | Alan Fan Pei |  |
| 沈阳 | [原文件](https://commons.wikimedia.org/wiki/File:Shenyang_Metro_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1304×1110 | 2025-07-13 | Painjet |  |
| 长春 | [原文件](https://commons.wikimedia.org/wiki/File:Changchun_Rapid_Transit_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 3374×3039 | 2026-09-07 | Kmchang28 |  |
| 大连 | [原文件](https://commons.wikimedia.org/wiki/File:Dalian_Metro_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 2884×2079 | 2023-04-29 | Kmchang28 |  |
| 哈尔滨 | [原文件](https://commons.wikimedia.org/wiki/File:Harbin_Metro_Diagram.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 3480×5105 | 2024-04-15 | Painjet |  |
| 苏州 | [原文件](https://commons.wikimedia.org/wiki/File:Suzhou_Metro_Network.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 3400×3400 | 2025-04-04 | Alan Fan Pei |  |
| 郑州 | [原文件](https://commons.wikimedia.org/wiki/File:Zhengzhou_Metro_Network.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 3400×3400 | 2025-10-28 | Alan Fan Pei |  |
| 昆明 | [原文件](https://commons.wikimedia.org/wiki/File:Kunming_Metro_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1875×1928 | 2022-12-14 | Kmchang28 |  |
| 杭州 | [原文件](https://commons.wikimedia.org/wiki/File:Hangzhou_Metro_Simplified_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 2098×1333 | 2026-01-02 | 刻晴 |  |
| 佛山 | [原文件](https://commons.wikimedia.org/wiki/File:Guangzhou-Foshan_Metro_Diagram_by_Tim.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 2848×2054 | 2026-07-30 | Tim Wu |  |
| 宁波 | [原文件](https://commons.wikimedia.org/wiki/File:Ningbo_Rail_Transit_System_Map_zh-hans.svg) · [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0) | SVG 2827×2275 | 2026-01-20 | Siyuwj |  |
| 无锡 | [原文件](https://commons.wikimedia.org/wiki/File:Wuxi_Metro_Network.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 2000×2200 | 2024-09-15 | Alan Fan Pei |  |
| 南昌 | [原文件](https://commons.wikimedia.org/wiki/File:Nanchang_Metro_Route_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 3315×3426 | 2025-08-25 | Kmchang28 |  |
| 兰州 | [原文件](https://commons.wikimedia.org/wiki/File:Lanzhou_Metro_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 900×368 | 2024-11-17 | Painjet |  |
| 青岛 | [原文件](https://commons.wikimedia.org/wiki/File:Qingdao_Metro_Map_zh.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1975×1410 | 2024-12-17 | Painjet |  |
| 福州 | [原文件](https://commons.wikimedia.org/wiki/File:Map_of_Fuzhou_Metro.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 4251×2834 | 2025-12-08 | SCJiang |  |
| 东莞 | [原文件](https://commons.wikimedia.org/wiki/File:Dongguan_Rail_Transit_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 3477×2597 | 2025-12-01 | Kmchang28 |  |
| 南宁 | [原文件](https://commons.wikimedia.org/wiki/File:Nanning_Rail_Transit_Diagram.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 5455×4020 | 2026-05-06 | Painjet |  |
| 合肥 | [原文件](https://commons.wikimedia.org/wiki/File:%E5%90%88%E8%82%A5%E8%BD%A8%E9%81%93%E4%BA%A4%E9%80%9A2024%E5%B9%B4%E8%BF%90%E8%90%A5%E7%BA%BF%E7%BD%91%E5%9B%BE.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 6000×4000 | 2025-02-08 | NTooru | 当前可用图源为2024年运营线网，可能尚未包含此后新线；请结合运营方公告。 |
| 石家庄 | [原文件](https://commons.wikimedia.org/wiki/File:Shijiazhuang_Metro_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1942×1725 | 2022-12-14 | Kmchang28 |  |
| 贵阳 | [原文件](https://commons.wikimedia.org/wiki/File:Guiyang_Urban_Rail_Transit_Map.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 7016×9922 | 2024-12-28 | Buernia |  |
| 厦门 | [原文件](https://commons.wikimedia.org/wiki/File:Xiamen_AMTR_Linemap.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1767×1148 | 2021-06-24 | Yveltal |  |
| 乌鲁木齐 | [原文件](https://commons.wikimedia.org/wiki/File:Urumqi_Metro_System_Map_2025.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1513×2050 | 2025-04-18 | Kmchang28 |  |
| 温州 | [原文件](https://commons.wikimedia.org/wiki/File:The_Wenzhou_Rail_Transit_System_Map_2025.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 4128×4014 | 2025-05-11 | Kmchang28 |  |
| 济南 | [原文件](https://commons.wikimedia.org/wiki/File:%E6%B5%8E%E5%8D%97%E5%9C%B0%E9%93%81%E7%BA%BF%E8%B7%AF%E5%9B%BE2025.12.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 3840×2914 | 2025-12-24 | J0eyShao |  |
| 常州 | [原文件](https://commons.wikimedia.org/wiki/File:Changzhou_Metro_System_Map_2025.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1735×2769 | 2025-04-26 | Kmchang28 |  |
| 徐州 | [原文件](https://commons.wikimedia.org/wiki/File:Xuzhou_Metro_System_Map.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 10466×9408 | 2026-01-10 | Yh6987tla , Pandavid233 OpenStreetMap contributors |  |
| 天水 | [原文件](https://commons.wikimedia.org/wiki/File:Tianshui_Tram_network_map.svg) · [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.en) | SVG 1523×323 | 2020-12-12 | Pieceofmetalwork | 当前可用图源为早期有轨电车示意图，可能尚未包含2025年延伸段。 |
| 太原 | [原文件](https://commons.wikimedia.org/wiki/File:Taiyuan_Metro_System_Map_2025.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1955×2385 | 2025-11-28 | Kmchang28 |  |
| 绍兴 | [原文件](https://commons.wikimedia.org/wiki/File:Shaoxing_Metro_Simplified_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1389×1009 | 2025-06-29 | 刻晴 |  |
| 芜湖 | [原文件](https://commons.wikimedia.org/wiki/File:%E8%8A%9C%E6%B9%96%E8%BD%A8%E4%BA%A4%E8%BF%90%E8%90%A5%E7%BA%BF%E7%BD%91.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 900×1200 | 2021-07-19 | NTooru |  |
| 南通 | [原文件](https://commons.wikimedia.org/wiki/File:Nantong_Rail_Transit_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 2158×2238 | 2025-08-26 | Kmchang28 |  |
| 台州 | [原文件](https://commons.wikimedia.org/wiki/File:Taizhou_Rail_Transit_System_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 1696×3581 | 2025-08-31 | Kmchang28 |  |
| 滁州 | [原文件](https://commons.wikimedia.org/wiki/File:Nanjing_Metro_Line_S4_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 971×489 | 2023-07-08 | 长安街上的灯 | 滁宁城际（南京地铁S4号线）线路图。 |
| 香港 | [原文件](https://commons.wikimedia.org/wiki/File:Hong_Kong_Railway_Route_Map_zh-hans.svg) · [Public domain](https://commons.wikimedia.org/wiki/Commons:Licensing) | SVG 1038×950 | 2025-04-04 | Sameboat and others |  |
| 澳门 | [原文件](https://commons.wikimedia.org/wiki/File:MLRT_Route_Map_Current.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 2500×4500 | 2025-07-13 | Fei0316 |  |
| 台北 | [原文件](https://commons.wikimedia.org/wiki/File:Taipei_Metro_official_map_optimised.png) · [Attribution](https://commons.wikimedia.org/wiki/File:Taipei_Metro_official_map_optimised.png#Licensing) | PNG 5867×8000 | 2026-08-30 | Own work derived from that of Taipei Rapid Transit Corporation |  |
| 新北 | [原文件](https://commons.wikimedia.org/wiki/File:Taipei_Metro_official_map_optimised.png) · [Attribution](https://commons.wikimedia.org/wiki/File:Taipei_Metro_official_map_optimised.png#Licensing) | PNG 5867×8000 | 2026-08-30 | Own work derived from that of Taipei Rapid Transit Corporation |  |
| 台中 | [原文件](https://commons.wikimedia.org/wiki/File:Taichung_Metro_Green_Line_Map.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 99×15 | 2020-11-14 | Cbliu |  |
| 高雄 | [原文件](https://commons.wikimedia.org/wiki/File:Kaohsiung_Metro_Line_Map-Have_Water_%28Text_Image%29.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 596×843 | 2024-08-24 | 鐵路Railway |  |
| 许昌 | [原文件](https://commons.wikimedia.org/wiki/File:Zhengzhou_Metro_Network.png) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | PNG 3400×3400 | 2025-10-28 | Alan Fan Pei | 郑州与许昌共用区域线网图；图中右下角为郑许线和许昌各站。 |
| 桃园 | [原文件](https://commons.wikimedia.org/wiki/File:Taoyuan_International_Airport_Access_MRT_System_Map_in_operation.svg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0) | SVG 788×433 | 2017-02-26 | QFA7301 | 机场捷运早期运营图；此源尚未包含2023年老街溪站延伸段，出行前请查询运营方。 |


## 覆盖缺口与核对依据


- 运营城市基准：中国城市轨道交通协会《2025年中国内地城轨交通线路概况》，截至2025年12月31日58城：https://infosharingp2-oss.camet.org.cn/resources/manual/2026/01/04/759313381470277.pdf 。此名单包含地铁、轻轨、市域快轨、有轨电车以及智轨等多种制式，不包括香港、澳门和台湾。

- 当前默认城市中的呼伦贝尔、阿尔山、满洲里、鄂尔多斯不在上述已运营城轨城市名单内，本目录不为这些城市套用其他城市的线路图。

- 各城市选用已核验来源中可获得的运营图；同一城市线网可能跨行政区，因此广州和佛山、台北和新北、郑州和许昌分别共用相应一张整网图。滁州采用滁宁城际S4号线图。

- 暂未收录完整且可明确再分发的高清整网图：淮安、三亚、株洲、宜宾、嘉兴、文山州、南平、金华、黄石、盐城、红河州。缺图不表示当地没有城市轨道交通。

- 部分图源反映较早的运营范围（如合肥2024年、天水早期线路）；本地下载和检查更新只核对源文件的版本，不代替运营方实时运营信息。

- 桃园机场捷运图源为早期运营版本，尚未包含2023年老街溪站延伸段；已在对应图源备注标明。
