package com.globexbank.qa.steps;

import io.cucumber.java.After;
import io.cucumber.java.Before;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;

public class Hooks {
    private static WebDriver driver;
    public static WebDriver driver() { return driver; }

    @Before
    public void start() { driver = new ChromeDriver(); driver.get("https://uat.globexbank.internal/online-banking"); }

    @After
    public void stop() { driver.quit(); }
}
