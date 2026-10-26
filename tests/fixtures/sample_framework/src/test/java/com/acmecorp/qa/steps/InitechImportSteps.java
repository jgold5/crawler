package com.acmecorp.qa.steps;

import com.acmecorp.qa.config.Config;
import com.acmecorp.qa.driver.DriverFactory;
import io.cucumber.java.en.Given;
import org.openqa.selenium.By;

public class InitechImportSteps {
    @Given("an Initech import file is uploaded")
    public void initechImportUploaded() throws Exception {
        DriverFactory.get().findElement(By.id("upload")).sendKeys(Config.IMPORT_PATH);
    }
}
