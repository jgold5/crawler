package com.acmecorp.qa.driver;

import java.net.URL;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.remote.RemoteWebDriver;

public final class DriverFactory {
    private static final String GRID_URL = "http://10.42.7.19:4444/wd/hub";
    public static final String BASE_URL = "https://qa.internal.acmecorp.com";
    private static final ThreadLocal<WebDriver> DRIVER = new ThreadLocal<>();

    private DriverFactory() {
    }

    public static WebDriver get() throws Exception {
        if (DRIVER.get() == null) {
            ChromeOptions options = new ChromeOptions();
            options.addArguments("--headless=new", "--window-size=1440,900");
            DRIVER.set(new RemoteWebDriver(new URL(GRID_URL), options));
            DRIVER.get().get(BASE_URL);
        }
        return DRIVER.get();
    }
}
