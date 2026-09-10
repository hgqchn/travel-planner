"use strict";

window.TripTaxonomy = (() => {
  let config = { categories: {}, suggested_tags: {}, max_tags: 30, max_tag_length: 24 };
  const configure = value => { if (value?.categories) config = value; };
  const categoryOptions = kind => [{ value: "", label: "未分类 / 待确认" },
    ...Object.keys(config.categories[kind] || {}).map(value => ({ value, label: value }))];
  const parseTags = value => {
    const parts = Array.isArray(value) ? value : String(value || "").split(/[,，、;；\n]+/u);
    const seen = new Set();
    return parts.filter(tag => typeof tag === "string").map(tag => tag.normalize("NFKC").replace(/\s+/gu, " ").trim())
      .filter(tag => { const key = tag.toLocaleLowerCase(); if (!tag || seen.has(key)) return false; seen.add(key); return true; });
  };
  const formatTags = value => parseTags(value).join("，");
  const effectiveTags = item => parseTags([...(Array.isArray(item.tags) ? item.tags : []), ...(item.cuisine ? [item.cuisine] : [])]);

  function enhanceTags(wrapper, input, kind) {
    const selected = element("div", "tag-editor-selected");
    const choices = element("div", "tag-editor-suggestions");
    const help = element("small", "", `逗号、顿号或换行分隔；最多 ${config.max_tags} 个，每个 ${config.max_tag_length} 字。点击标签可移除。`);
    const update = () => {
      const tags = parseTags(input.value);
      const invalid = tags.length > config.max_tags ? `最多添加 ${config.max_tags} 个标签。`
        : tags.some(tag => [...tag].length > config.max_tag_length) ? `每个标签最多 ${config.max_tag_length} 个字符。` : "";
      input.setCustomValidity(invalid);
      selected.replaceChildren(...tags.map(tag => {
        const button = element("button", "tag-editor-chip", `${tag} ×`);
        button.type = "button";
        button.setAttribute("aria-label", `移除标签：${tag}`);
        button.addEventListener("click", () => {
          input.value = formatTags(parseTags(input.value).filter(value => value !== tag));
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.focus();
        });
        return button;
      }));
      choices.replaceChildren(...(config.suggested_tags[kind] || []).filter(tag => !tags.includes(tag)).map(tag => {
        const button = element("button", "tag-suggestion", `+ ${tag}`);
        button.type = "button";
        button.disabled = tags.length >= config.max_tags;
        button.setAttribute("aria-label", `添加标签：${tag}`);
        button.addEventListener("click", () => {
          input.value = formatTags([...parseTags(input.value), tag]);
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.focus();
        });
        return button;
      }));
    };
    input.addEventListener("input", update);
    wrapper.append(help, selected, choices);
    update();
  }

  return { configure, categoryOptions, parseTags, formatTags, effectiveTags, enhanceTags };
})();
