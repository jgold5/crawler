package com.globexbank.qa.pages;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.openqa.selenium.By;
import org.yaml.snakeyaml.Yaml;

/** Loads per-page YAML definitions from src/test/resources/pages (owner: ops.desk@globexbank.com). */
public class PageRegistry {
    private static final List<String> ELEMENT_TYPES =
            Arrays.asList("button", "textfield", "dropdown", "date", "link", "row", "cell");
    private final Map<String, Map<String, Object>> pages = new HashMap<>();

    public Map<String, Object> getPage(String pageKey) {
        return pages.computeIfAbsent(pageKey, this::loadPage);
    }

    private Map<String, Object> loadPage(String pageKey) {
        Map<String, Object> raw = loadWithBase(pageKey, new ArrayList<>());
        Map<String, Object> page = new HashMap<>();
        page.put("menuPath", raw.get("menuPath"));
        page.put("iframe", raw.get("iframe"));
        return page;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> loadWithBase(String pageKey, List<String> chain) {
        chain.add(pageKey);
        InputStream in = getClass().getResourceAsStream("/pages/" + pageKey + ".yaml");
        Map<String, Object> def = new Yaml().load(in);
        Object base = def.get("extends");
        if (base != null && !chain.contains(base)) {
            Map<String, Object> merged = new HashMap<>(loadWithBase((String) base, chain));
            merged.putAll(def);
            return merged;
        }
        return def;
    }

    public By getLocator(String pageKey, String elementName, Map<String, String> params) {
        Map<String, Object> el = (Map<String, Object>) ((Map<String, Object>) getPage(pageKey).get("elements")).get(elementName);
        String xpath = (String) el.get("xpath");
        for (Map.Entry<String, String> p : params.entrySet()) {
            xpath = xpath.replace("{" + p.getKey() + "}", p.getValue());
        }
        return By.xpath(xpath);
    }

    public By getIframeLocator(String pageKey) {
        return By.id((String) getPage(pageKey).get("iframe"));
    }
}
