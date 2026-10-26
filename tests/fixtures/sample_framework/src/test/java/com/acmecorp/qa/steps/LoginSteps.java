package com.acmecorp.qa.steps;

import static org.junit.Assert.assertEquals;

import com.acmecorp.qa.driver.DriverFactory;
import com.acmecorp.qa.pages.LoginPage;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;

public class LoginSteps {
    private final WebDriver driver;
    private final LoginPage loginPage;

    public LoginSteps() throws Exception {
        driver = DriverFactory.get();
        loginPage = new LoginPage(driver);
    }

    @Given("^I am on the AcmeCorp login page$")
    public void iAmOnTheLoginPage() {
        loginPage.open();
    }

    @When("I enter {string} as the username")
    public void iEnterUsername(String user) {
        loginPage.enterUsername(user);
    }

    @When("^I type \"([^\"]*)\" into the \"([^\"]*)\" field$")
    public void iTypeIntoField(String text, String label) {
        driver.findElement(By.xpath("//label[normalize-space()='" + label + "']/following::input[1]")).sendKeys(text);
    }

    @When("I click sign in")
    public void iClickSignIn() {
        loginPage.signInButton.click();
    }

    @Then("the AcmeCorp dashboard greets {string}")
    public void dashboardGreets(String expected) {
        assertEquals(expected, loginPage.welcomeText());
    }

    @Given("I log in as {string} with password {string}")
    public void iLogInAs(String user, String secret) {
        loginPage.loginAs(user, secret);
    }

    @When("I open {string}")
    public void iOpen(String url) {
        driver.navigate().to(url);
    }
}
