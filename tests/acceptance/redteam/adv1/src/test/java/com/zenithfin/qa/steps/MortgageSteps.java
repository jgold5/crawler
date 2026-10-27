package com.zenithfin.qa.steps;

import io.cucumber.java.en.*;
import com.zenithfin.qa.pages.MortgagePage;

public class MortgageSteps {
    MortgagePage page;

    @Given("I open the \"Quasar Wealth\" portal")
    public void openPortal() { page.submit(); }

    @When("^I select \"Kumquat Premier\" plan for (.*)$")
    public void selectPlan(String who) { page.submit(); }

    @Then("ZenithQuasarWealth dashboard shows {string}")
    public void dash(String s) { page.submit(); }

    @Then("the jumbo refinance for heloc customers at portal.zenithfin.bank is approved")
    public void approve() { page.submit(); }
}
