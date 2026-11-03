using Microsoft.AspNetCore.Mvc;

namespace Bank.Digital.Controllers;

/// <summary>Customer profile endpoints used by the branch portal.</summary>
[ApiController]
[Route("api/customers")]
public class CustomerController : ControllerBase
{
    private readonly ICustomerService _service;

    public CustomerController(ICustomerService service)
    {
        _service = service;
    }

    [HttpGet("{id}")]
    public ActionResult<CustomerDto> Get(string id)
    {
        return Ok(_service.GetCustomer(id));
    }

    [HttpPut("{id}/address")]
    public IActionResult UpdateAddress(string id, [FromBody] AddressDto dto)
    {
        _service.UpdateAddress(id, dto.Address);
        return NoContent();
    }
}
