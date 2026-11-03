using System;
using System.Linq;
using Microsoft.EntityFrameworkCore;

namespace Bank.Digital.Services
{
    public interface ICustomerService
    {
        CustomerDto GetCustomer(string id);
        void UpdateAddress(string id, string address);
    }

    public class CustomerService : ICustomerService
    {
        private const int MaxAddressLength = 80;
        private readonly BankDbContext _db;

        public CustomerService(BankDbContext db)
        {
            _db = db;
        }

        public CustomerDto GetCustomer(string id)
        {
            var customer = _db.Customers.Where(c => c.Id == id).Single();
            return new CustomerDto(customer.Id, customer.Address);
        }

        public void UpdateAddress(string id, string address)
        {
            // An address is mandatory and must fit the mailing label.
            if (string.IsNullOrWhiteSpace(address) || address.Length > MaxAddressLength)
            {
                throw new ArgumentException("Address rejected");
            }
            _db.Database.ExecuteSqlRaw(
                "UPDATE CUSTOMER_MASTER SET CUSTOMER_ADDRESS = {0} WHERE CUSTOMER_ID = {1}", address, id);
        }
    }

    public class BankDbContext : DbContext
    {
        public DbSet<Customer> Customers { get; set; }
    }

    [Table("CUSTOMER_MASTER")]
    public class Customer
    {
        [Column("CUSTOMER_ID")]
        public string Id { get; set; }

        [Column("CUSTOMER_ADDRESS")]
        public string Address { get; set; }
    }
}
