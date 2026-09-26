var builder = DistributedApplication.CreateBuilder(args);

var basket = builder.AddProject<Projects.Basket_Api>("basket-api");
builder.AddProject<Projects.Web>("web-frontend").WithReference(basket);
builder.AddNpmApp("storefront", "../../frontend");

builder.Build().Run();
