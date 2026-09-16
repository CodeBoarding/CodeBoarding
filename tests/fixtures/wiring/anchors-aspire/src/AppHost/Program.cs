var builder = DistributedApplication.CreateBuilder(args);

var identityApi = builder.AddProject<Projects.Identity_Api>("identity-api");
var identityEndpoint = identityApi.GetEndpoint("https");

var basketApi = builder.AddProject<Projects.Basket_Api>("basket-api")
    .WithEnvironment("Identity__Url", identityEndpoint);

var gateway = builder.AddYarp("mobile-bff");
var basketCluster = gateway.AddCluster(basketApi);
gateway.AddRoute("/basket-api/items/{id}", basketCluster);
gateway.AddRoute("/basket-api/items/by-brand/{brandId?}", basketCluster);

builder.Build().Run();
